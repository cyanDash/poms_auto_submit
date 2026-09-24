#!/usr/bin/env python
"""Check progress, decide whether to submit the next
slice of a POMS campaign stage, update stage params if needed, and submit.

Intended to run from crontab, e.g.:
    0 * * * * /path/to/scripts/poms_auto_submit.py -c /path/to/configs/config.ini 2>&1
"""

import argparse
import configparser
import fcntl
import json
import logging
import os
import sys

import cleanup
import condor_progress
import recovery
import stragglers
from poms_client_bootstrap import setup_poms_client_path
from poms_session import ACTIVE_SUBMISSION_STATUSES, PRO_SUBGROUP, PomsSession

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# Only role sbndpro's managed token can auth as; see docs/adr/0004.
PRO_ELIGIBLE_ROLE = "production"

# Consecutive runs a POMS-active Submission may sit in one Status with no
# condor_q data before a manual-intervention WARNING is logged every run.
STUCK_NO_DATA_RUNS_WARN = 5

# Per-Submission gate for _cleanup_ready(); see docs/adr/0016-cleanup-gates-on-last-slice-completion.md.
CLEANUP_PCT_COMPLETE_THRESHOLD = 98


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise FileNotFoundError(f"could not read config file: {path}")

    setup_poms_client_path()

    campaign_name = parser.get("poms", "campaign_name")
    # One directory per campaign under logs/, holding everything campaign-related
    # (log, lock, cache jsons, output_definitions_*.txt) except the config itself.
    campaign_dir = os.path.join(os.path.dirname(path), "..", "logs", campaign_name)
    os.makedirs(campaign_dir, exist_ok=True)

    cfg = {
        "experiment": parser.get("poms", "experiment"),
        "role": PRO_ELIGIBLE_ROLE,
        "campaign_name": campaign_name,
        "campaign_stage_name": parser.get("poms", "campaign_stage_name"),
        "switch": parser.getboolean("decision", "switch", fallback=True),
        "pct_complete_threshold": parser.getfloat("decision", "pct_complete_threshold"),
        "submit_two_slices": parser.getboolean("decision", "submit_two_slices", fallback=False),
        "max_splits": parser.getint("decision", "max_splits"),
        "last_split": parser.getint("decision", "last_split"),
        "test_launch": parser.getboolean("decision", "test_launch", fallback=False),
        "recovery_handled": parser.getboolean("decision", "recovery_handled", fallback=False),
        # Required on this branch: split_type=None for this campaign stage,
        # so slices are pre-built by hand. See docs/adr/0014.
        "input_dataset_template": parser.get("decision", "input_dataset_template"),
        "do_cleanup": parser.getboolean("decision", "do_cleanup", fallback=False),
        "log_file": os.path.join(campaign_dir, "poms_auto_submit.log"),
        "lock_file": os.path.join(campaign_dir, "poms_auto_submit.lock"),
        "config_path": os.path.abspath(path),
    }
    # PomsSession's cache dir; see docs/adr/0008-cache-static-submission-fields.md.
    cfg["cache_dir"] = campaign_dir
    return cfg


def _persist_config_value(config_path, section, key, value):
    """Rewrite one key in place in config_path; leaves comments/rest untouched."""
    with open(config_path) as f:
        lines = f.readlines()

    current_section = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = stripped[1:-1]
            continue
        if current_section == section and stripped.split("=", 1)[0].strip() == key:
            lines[i] = f"{key} = {value}\n"
            break
    else:
        raise RuntimeError(f"{key} key not found in [{section}] section of {config_path}")

    with open(config_path, "w") as f:
        f.writelines(lines)


def persist_last_split(config_path, last_split):
    _persist_config_value(config_path, "decision", "last_split", last_split)


def persist_recovery_handled(config_path, value):
    _persist_config_value(config_path, "decision", "recovery_handled", int(bool(value)))


def persist_switch(config_path, value):
    _persist_config_value(config_path, "decision", "switch", int(bool(value)))


def acquire_lock(lock_path):
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()
    return lock_fh


def _in_flight_submissions(cfg, submissions, get_condor_progress=None, threshold=None, stuck_stage_id=None):
    """Submissions still holding a slot, decided from condor_q; POMS Status
    is only the tiebreak when condor_q has no data. See
    docs/adr/0005-in-flight-slot-based-decision.md."""
    get_condor_progress = get_condor_progress or condor_progress.get_progress
    threshold = cfg["pct_complete_threshold"] if threshold is None else threshold
    in_flight = []
    no_data = []
    for s in submissions:
        jobsub_job_id = s.get("jobsub_job_id")
        if jobsub_job_id:
            progress = get_condor_progress(cfg["experiment"], jobsub_job_id)
        else:
            progress = condor_progress.Progress("no_data")
        if progress.outcome == "error":
            logging.warning(
                "condor_q failed for submission_id=%s -- holding every submission in the window",
                s.get("submission_id"),
            )
            return list(submissions)
        if progress.outcome == "no_data" and s.get("status") in ACTIVE_SUBMISSION_STATUSES:
            no_data.append(s)
        if _holds_slot(s, progress, threshold):
            in_flight.append(s)
    if stuck_stage_id is not None:
        _update_stuck_counts(cfg, stuck_stage_id, no_data)
    return in_flight


def _stuck_cache_file(cfg, campaign_stage_id):
    cache_dir = cfg.get("cache_dir")
    if not cache_dir:
        return None
    return os.path.join(cache_dir, f"stuck_no_data_{campaign_stage_id}.json")


def _update_stuck_counts(cfg, campaign_stage_id, no_data):
    """Count consecutive runs each POMS-active Submission has sat in the same
    Status with no condor_q data; WARN from STUCK_NO_DATA_RUNS_WARN on. Never
    frees the slot. Anything not in `no_data` is dropped, which resets it."""
    cache_file = _stuck_cache_file(cfg, campaign_stage_id)
    if not cache_file:
        return
    try:
        with open(cache_file) as f:
            previous = json.load(f)
        previous = {sid: e for sid, e in previous.items() if isinstance(e, dict) and isinstance(e.get("count"), int)}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        previous = {}

    current = {}
    for s in no_data:
        submission_id = str(s.get("submission_id"))
        prior = previous.get(submission_id)
        count = prior["count"] + 1 if prior and prior["status"] == s.get("status") else 1
        current[submission_id] = {"status": s.get("status"), "count": count}
        if count >= STUCK_NO_DATA_RUNS_WARN:
            logging.warning(
                "submission_id=%s has been %s with no condor_q data for %d consecutive runs "
                "-- manual intervention needed",
                submission_id, s.get("status"), count,
            )

    with open(cache_file, "w") as f:
        json.dump(current, f, indent=2)


def _holds_slot(s, progress, threshold):
    active = s.get("status") in ACTIVE_SUBMISSION_STATUSES
    if progress.outcome == "live":
        holds = progress.pct < threshold
    elif progress.outcome == "finished":
        holds = False
    else:
        holds = active
    _log_live(s, progress)
    return holds


def _log_live(s, progress):
    """Only Submissions condor_q reports as live are logged; POMS Status is
    deliberately left out, see docs/adr/0017."""
    if progress.outcome != "live":
        return
    logging.info(
        "progress: submission_id=%s status=%s completion_%%=%.2f jobsub_job_id=%s subgroup=%s",
        s.get("submission_id"), progress.job_status, progress.pct, s.get("jobsub_job_id"), s.get("subgroup"),
    )


def _no_splits_left(cfg):
    return cfg["max_splits"] - cfg["last_split"] <= 0


def _plan(cfg, submissions, get_condor_progress, stuck_stage_id=None):
    """Shared by _next_slice_count() and plan_next_slices(). Returns (num_slices, in_flight)."""
    if _no_splits_left(cfg):
        logging.info(
            "decision: skip (max_splits reached: last_split=%d max_splits=%d)",
            cfg["last_split"], cfg["max_splits"],
        )
        return 0, []

    remaining_splits = cfg["max_splits"] - cfg["last_split"]
    target = 2 if cfg["submit_two_slices"] else 1
    in_flight = _in_flight_submissions(cfg, submissions, get_condor_progress, stuck_stage_id=stuck_stage_id)
    num_slices = min(max(0, target - len(in_flight)), remaining_splits)
    subgroup_plan = _plan_subgroups(num_slices, cfg["role"], _pro_available(in_flight))
    subgroup_plan = ["pro" if use_pro else "standard" for use_pro in subgroup_plan]
    logging.info(
        "decision: submit %d slice(s) (in_flight=%d target=%d remaining_splits=%d) subgroup=%s",
        num_slices, len(in_flight), target, remaining_splits, subgroup_plan,
    )
    return num_slices, in_flight


def _next_slice_count(cfg, submissions, get_condor_progress=None):
    """Decide how many new slices to submit this run (0, 1, or 2): enough to
    bring the in-flight count up to target, capped by remaining_splits."""
    return _plan(cfg, submissions, get_condor_progress)[0]


def _pro_available(in_flight):
    """Whether the campaign's single pro slot is free; see CONTEXT.md's
    Subgroup entry."""
    return not any(s.get("subgroup") == PRO_SUBGROUP for s in in_flight)


def _plan_subgroups(num_slices, role, pro_available):
    """Decide which subgroup each new submission gets; see
    docs/adr/0002-lone-slice-defaults-to-pro-subgroup.md."""
    if num_slices == 0:
        return []
    if role != PRO_ELIGIBLE_ROLE or not pro_available:
        return [False] * num_slices
    return [True] + [False] * (num_slices - 1)


def plan_next_slices(cfg, session, get_condor_progress=None, dry_run=False):
    """Decide how many new slices to submit this run and which subgroup each
    gets. Returns a list with one entry per slice (True = pro, False =
    standard), possibly empty."""
    submissions = session.get_progress()

    stuck_stage_id = None if dry_run else getattr(session, "campaign_stage_id", None)
    num_slices, in_flight = _plan(cfg, submissions, get_condor_progress, stuck_stage_id)
    if num_slices == 0:
        return []

    return _plan_subgroups(num_slices, cfg["role"], _pro_available(in_flight))


def _any_still_running(cfg, submissions, get_condor_progress=None, threshold=float("inf")):
    """Whether any Submission in the window is still running, judged by
    condor_q on every one. A live DAG below `threshold`, a POMS-active
    Submission with no data, or a condor_q error all count as running.
    The default threshold makes a live DAG at any percent count."""
    return bool(_in_flight_submissions(cfg, submissions, get_condor_progress, threshold))


def _cleanup_ready(cfg, session, get_condor_progress=None):
    """Whether the campaign is done enough to safely run duplicate-cleanup
    and turn the campaign stage off; see
    docs/adr/0016-cleanup-gates-on-last-slice-completion.md."""
    if not (cfg["do_cleanup"] and cfg["recovery_handled"] and _no_splits_left(cfg)):
        return False

    submissions = session.get_progress()
    if not submissions:
        return False

    return not _any_still_running(cfg, submissions, get_condor_progress, CLEANUP_PCT_COMPLETE_THRESHOLD)


def _manage_stragglers(cfg, session, dry_run):
    try:
        submissions = session.get_progress()
    except RuntimeError:
        logging.exception("could not fetch POMS progress -- skipping straggler check")
        return
    try:
        stragglers.manage(cfg, getattr(session, "campaign_stage_id", None), submissions, dry_run=dry_run)
    except OSError:
        logging.exception("straggler check failed -- continuing with cleanup and recovery")


def submit_plan(cfg, session, plan):
    """Submit each planned slice in order (pointing the stage at its
    pre-built slice dataset first; see docs/adr/0014), persisting last_split
    after each success. Shared with recovery.py; see docs/adr/0012. Returns False if
    POMS reported the campaign stage exhausted partway through."""
    for use_pro in plan:
        dataset_name = cfg["input_dataset_template"].format(n=cfg["last_split"])
        session.set_input_dataset(dataset_name)
        session.set_subgroup(use_pro)
        submission_id = session.submit_next_slice()
        if submission_id is None:
            logging.info(
                "submit_next_slice returned no submission (campaign stage exhausted) "
                "-- stopping further slice submissions this run"
            )
            return False
        cfg["last_split"] += 1
        persist_last_split(cfg["config_path"], cfg["last_split"])
    return True


def run(cfg, dry_run):
    import poms_client as pc

    session = PomsSession(pc, cfg)

    try:
        plan = plan_next_slices(cfg, session, dry_run=dry_run)
    except RuntimeError:
        # Non-2xx HTTP (e.g. expired token); skip this cycle, retry next hour.
        logging.exception("could not fetch POMS progress -- skipping this run")
        return
    if _no_splits_left(cfg):
        _manage_stragglers(cfg, session, dry_run)
    if not plan:
        if dry_run:
            if _cleanup_ready(cfg, session):
                logging.info("dry-run: would run duplicate-cleanup and turn switch off")
        elif _cleanup_ready(cfg, session):
            cleanup.run_cleanup(cfg, session)
        return

    if dry_run:
        subgroup_plan = ["pro" if use_pro else "standard" for use_pro in plan]
        datasets = [cfg["input_dataset_template"].format(n=cfg["last_split"] + i) for i in range(len(plan))]
        logging.info(
            "dry-run: would submit %d slice(s) with subgroup plan=%s against dataset(s)=%s",
            len(plan), subgroup_plan, datasets,
        )
        return

    if not submit_plan(cfg, session, plan):
        recovery.evaluate_and_run_recovery(cfg, session)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=os.path.join(REPO_ROOT, "configs", "config.ini"))
    parser.add_argument("--dry-run", action="store_true", help="log decisions without updating params or submitting")
    args = parser.parse_args()

    cfg = load_config(args.config)

    handlers = [logging.FileHandler(cfg["log_file"]), logging.StreamHandler()]
    # Silences noisy INFO logs from poms_client's vendored urllib3 on every
    # reused-but-dropped connection to POMS.
    for handler in handlers:
        handler.addFilter(
            lambda record: "Resetting dropped connection" not in record.getMessage()
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    logging.info("===== poms_auto_submit run start =====")
    try:
        if not cfg["switch"]:
            logging.info("switch is off (switch=0 in config), skipping this run")
            return 0

        lock_fh = acquire_lock(cfg["lock_file"])
        if lock_fh is None:
            logging.info("previous run still active (lock held), skipping this run")
            return 0

        try:
            run(cfg, args.dry_run)
        except Exception:
            logging.exception("poms_auto_submit run failed")
            return 1
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

        return 0
    finally:
        logging.info("===== poms_auto_submit run end =====")


if __name__ == "__main__":
    sys.exit(main())
