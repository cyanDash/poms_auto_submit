#!/usr/bin/env python
"""Check progress, decide whether to submit the next
slice of a POMS campaign stage, update stage params if needed, and submit.

Intended to run from crontab, e.g.:
    0 * * * * /path/to/scripts/poms_auto_submit.py -c /path/to/configs/config.ini 2>&1
"""

import argparse
import configparser
import fcntl
import logging
import os
import sys
from datetime import datetime, timedelta

import condor_progress
from poms_client_bootstrap import setup_poms_client_path
from poms_session import PRO_SUBGROUP, PomsSession

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# Not configurable via config.ini: setup.sh only ever authenticates via
# sbndpro's production-role managed-token credkey, so this is the only role
# any POMS call from this script could succeed with anyway.
PRO_ELIGIBLE_ROLE = "production"

# Fallback layer 2, only used when condor_q is unavailable -- see
# docs/adr/0007-condor-q-primary-progress-source.md.
STALE_STATUS_HOURS = 2


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise FileNotFoundError(f"could not read config file: {path}")

    setup_poms_client_path()

    cfg = {
        "experiment": parser.get("poms", "experiment"),
        "role": PRO_ELIGIBLE_ROLE,
        "campaign_name": parser.get("poms", "campaign_name"),
        "campaign_stage_name": parser.get("poms", "campaign_stage_name"),
        "switch": parser.getboolean("decision", "switch", fallback=True),
        "pct_complete_threshold": parser.getfloat("decision", "pct_complete_threshold"),
        "submit_two_slices": parser.getboolean("decision", "submit_two_slices", fallback=False),
        "max_splits": parser.getint("decision", "max_splits"),
        "last_split": parser.getint("decision", "last_split"),
        "test_launch": parser.getboolean("decision", "test_launch", fallback=False),
        "log_file": os.path.join(os.path.dirname(path), parser.get("paths", "log_file")),
        "lock_file": os.path.join(os.path.dirname(path), parser.get("paths", "lock_file")),
        "config_path": os.path.abspath(path),
    }
    # PomsSession caches static per-submission fields at
    # <cache_dir>/<campaign_stage_id>.json -- see
    # docs/adr/0008-cache-static-submission-fields.md. Reuses log_file's
    # directory rather than adding a new [paths] key.
    cfg["cache_dir"] = os.path.dirname(cfg["log_file"])
    return cfg


def persist_last_split(config_path, last_split):
    """Write the updated last_split counter back to config.ini in place, leaving comments and everything else untouched."""
    with open(config_path) as f:
        lines = f.readlines()

    section = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if section == "decision" and stripped.split("=", 1)[0].strip() == "last_split":
            lines[i] = f"last_split = {last_split}\n"
            break
    else:
        raise RuntimeError(f"last_split key not found in [decision] section of {config_path}")

    with open(config_path, "w") as f:
        f.writelines(lines)


def acquire_lock(lock_path):
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()
    return lock_fh


def _stale_status_proxy_pct_complete(s, now):
    """Fallback layer 2 -- see docs/adr/0007-condor-q-primary-progress-source.md."""
    pct_complete = s["pct_complete"]
    last_status_change = s.get("last_status_change")
    if last_status_change is None or now - last_status_change < timedelta(hours=STALE_STATUS_HOURS):
        return pct_complete

    files_submitted = s.get("files_submitted")
    files_pending = s.get("files_pending")
    if not files_submitted:
        return pct_complete

    proxy = (files_submitted - files_pending) / files_submitted * 100
    logging.warning(
        "submission_id=%s: pct_complete=%s stale since %s (>%dh) -- using statuses-array proxy=%.2f "
        "(files_submitted=%d files_pending=%d)",
        s.get("submission_id"), pct_complete, last_status_change, STALE_STATUS_HOURS, proxy,
        files_submitted, files_pending,
    )
    return proxy


def _log_progress(s, pct_complete, source):
    # A submission past this point is effectively done -- logging it every
    # run just adds noise once there's nothing left to decide about it.
    if pct_complete is not None and pct_complete > 99:
        return
    logging.info(
        "progress: submission_id=%s status=%s pct_complete=%s (%s) jobsub_job_id=%s subgroup=%s",
        s.get("submission_id"), s.get("status"), pct_complete, source, s.get("jobsub_job_id"), s.get("subgroup"),
    )


def _effective_pct_complete(cfg, s, now, get_condor_pct_complete=None):
    """3-layer fallback chain -- see docs/adr/0007-condor-q-primary-progress-source.md.

    condor_q is tried whenever jobsub_job_id is present, regardless of
    whether pct_complete itself is known -- a submission whose static fields
    came from the local cache (see
    docs/adr/0008-cache-static-submission-fields.md) always has
    pct_complete=None, but still has a jobsub_job_id condor_q can resolve.
    Returns None only when neither condor_q nor a POMS-side pct_complete is
    available at all.
    """
    get_condor_pct_complete = get_condor_pct_complete or condor_progress.get_pct_complete
    condor_pct = get_condor_pct_complete(cfg["experiment"], s.get("jobsub_job_id"))
    if condor_pct is not None:
        effective, source = condor_pct, "condor_q"
    elif s.get("pct_complete") is not None:
        effective, source = _stale_status_proxy_pct_complete(s, now), "poms"
    else:
        _log_progress(s, None, "none")
        return None
    effective = round(effective, 2)
    _log_progress(s, effective, source)
    return effective


def in_flight_submissions(cfg, submissions, now=None, get_condor_pct_complete=None):
    """Active submissions still under pct_complete_threshold -- i.e. occupying
    a slot this run hasn't freed up yet (see CONTEXT.md's Status entry and
    docs/adr/0005-in-flight-slot-based-decision.md). No signal at all (neither
    condor_q nor POMS) is treated as still in-flight, conservatively."""
    now = now or datetime.now()
    threshold = cfg["pct_complete_threshold"]
    in_flight = []
    for s in submissions:
        effective = _effective_pct_complete(cfg, s, now, get_condor_pct_complete)
        if effective is None or effective < threshold:
            in_flight.append(s)
    return in_flight


def next_slice_count(cfg, submissions, now=None, get_condor_pct_complete=None):
    """Decide how many new slices to submit this run (0, 1, or 2): enough to
    bring the in-flight count up to target, capped by remaining_splits."""
    remaining_splits = cfg["max_splits"] - cfg["last_split"]
    if remaining_splits <= 0:
        logging.info(
            "decision: skip (max_splits reached: last_split=%d max_splits=%d)",
            cfg["last_split"], cfg["max_splits"],
        )
        return 0

    target = min(2 if cfg["submit_two_slices"] else 1, remaining_splits)
    in_flight = in_flight_submissions(cfg, submissions, now, get_condor_pct_complete)
    num_slices = max(0, target - len(in_flight))
    subgroup_plan = plan_subgroups(num_slices, cfg["role"], pro_available(in_flight))
    subgroup_plan = ["pro" if use_pro else "standard" for use_pro in subgroup_plan]
    logging.info(
        "decision: submit %d slice(s) (in_flight=%d target=%d) subgroup=%s",
        num_slices, len(in_flight), target, subgroup_plan,
    )
    return num_slices


def pro_available(in_flight):
    """Whether the campaign's single pro slot is free -- no in-flight
    submission already holds it (see CONTEXT.md's Subgroup entry: only one
    slice may hold pro at a time)."""
    return not any(s.get("subgroup") == PRO_SUBGROUP for s in in_flight)


def plan_subgroups(num_slices, role, pro_available):
    """Decide which subgroup each of the num_slices new submissions should use
    (see docs/adr/0002-lone-slice-defaults-to-pro-subgroup.md and
    docs/adr/0005-in-flight-slot-based-decision.md)."""
    if num_slices == 0:
        return []
    if role != PRO_ELIGIBLE_ROLE or not pro_available:
        return [False] * num_slices
    return [True] + [False] * (num_slices - 1)


def plan_next_slices(cfg, session, now=None, get_condor_pct_complete=None):
    """Decide how many new slices to submit this run and which subgroup each gets.

    Returns a list with one entry per slice to submit (True = pro subgroup,
    False = standard), possibly empty.
    """
    submissions = session.get_progress()

    num_slices = next_slice_count(cfg, submissions, now, get_condor_pct_complete)
    if num_slices == 0:
        return []

    in_flight = in_flight_submissions(cfg, submissions, now, get_condor_pct_complete)
    return plan_subgroups(num_slices, cfg["role"], pro_available(in_flight))


def run(cfg, dry_run):
    import poms_client as pc

    session = PomsSession(pc, cfg)

    try:
        plan = plan_next_slices(cfg, session)
    except RuntimeError:
        # poms_client raises RuntimeError on non-2xx HTTP (e.g. an expired
        # proxy/token, HTTP 403). Treat as a skip for this cycle rather than
        # a hard failure -- the next hourly run will pick up cleanly once
        # the token is renewed.
        logging.exception("could not fetch POMS progress -- skipping this run")
        return
    if not plan:
        return

    if dry_run:
        subgroup_plan = ["pro" if use_pro else "standard" for use_pro in plan]
        logging.info("dry-run: would submit %d slice(s) with subgroup plan=%s", len(plan), subgroup_plan)
        return

    for use_pro in plan:
        session.set_subgroup(use_pro)
        session.submit_next_slice()
        cfg["last_split"] += 1
        persist_last_split(cfg["config_path"], cfg["last_split"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=os.path.join(REPO_ROOT, "configs", "config.ini"))
    parser.add_argument("--dry-run", action="store_true", help="log decisions without updating params or submitting")
    args = parser.parse_args()

    cfg = load_config(args.config)

    handlers = [logging.FileHandler(cfg["log_file"]), logging.StreamHandler()]
    # poms_client pulls in requests v2.9.1, whose vendored
    # requests.packages.urllib3.connectionpool logs this at INFO on every
    # reused-but-dropped connection to POMS -- never actionable, just noise
    # from a long-lived process making many requests. Filtering on the
    # handlers (rather than that logger by name) also covers the real
    # urllib3.connectionpool, in case a future upgrade switches which one
    # actually opens the connection.
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
