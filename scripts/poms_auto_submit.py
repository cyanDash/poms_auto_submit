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

import cleanup
import recovery
import state
import stragglers
from campaign_snapshot import PRO_ELIGIBLE_ROLE, CampaignSnapshot, no_splits_left
from poms_client_bootstrap import setup_poms_client_path
from poms_session import PomsSession

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise FileNotFoundError(f"could not read config file: {path}")

    setup_poms_client_path()

    campaign_name = parser.get("poms", "campaign_name")
    # One directory per campaign under logs/, holding everything campaign-related
    # (log, lock, state_<ini name>.json) except the config itself.
    campaign_dir = os.path.join(os.path.dirname(path), "..", "logs", campaign_name)
    os.makedirs(campaign_dir, exist_ok=True)

    if not parser.has_option("decision", "input_dataset_template"):
        raise ValueError(
            f"{path}: [decision] input_dataset_template is required "
            "(e.g. base_name_slice{n}_files10000); see docs/adr/0022"
        )
    input_dataset_template = parser.get("decision", "input_dataset_template", raw=True)
    if "{n}" not in input_dataset_template:
        raise ValueError(f"{path}: input_dataset_template must contain the {{n}} slice placeholder")

    cfg = {
        "experiment": parser.get("poms", "experiment"),
        "role": PRO_ELIGIBLE_ROLE,
        "campaign_name": campaign_name,
        "campaign_stage_name": parser.get("poms", "campaign_stage_name"),
        "pct_complete_threshold": parser.getfloat("decision", "pct_complete_threshold"),
        "submit_two_slices": parser.getboolean("decision", "submit_two_slices", fallback=False),
        "max_splits": parser.getint("decision", "max_splits"),
        "input_dataset_template": input_dataset_template,
        "test_launch": parser.getboolean("decision", "test_launch", fallback=False),
        "do_recovery": parser.getboolean("decision", "do_recovery", fallback=False),
        "do_cleanup": parser.getboolean("decision", "do_cleanup", fallback=False),
        "log_file": os.path.join(campaign_dir, "poms_auto_submit.log"),
        "lock_file": os.path.join(campaign_dir, "poms_auto_submit.lock"),
    }
    cfg["cache_dir"] = campaign_dir
    # One state file per ini; see docs/adr/0020-one-consolidated-state-file-per-config.md.
    ini_name = os.path.splitext(os.path.basename(path))[0]
    cfg["state_file"] = os.path.join(campaign_dir, f"state_{ini_name}.json")
    # The ini values only seed an ini written before ADR-0020; once the state
    # file holds a value the ini's is ignored.
    cfg["switch"] = state.read(cfg, "switch", parser.getboolean("decision", "switch", fallback=True))
    cfg["last_split"] = state.read(cfg, "last_split", parser.getint("decision", "last_split", fallback=0))
    return cfg


def persist_last_split(cfg, last_split):
    state.write(cfg, "last_split", last_split)


def persist_switch(cfg, value):
    state.write(cfg, "switch", bool(value))


def acquire_lock(lock_path):
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()
    return lock_fh


def plan_next_slices(cfg, session, get_condor_progress=None, dry_run=False):
    """Decide how many new slices to submit this run and which subgroup each
    gets. Returns a list with one entry per slice (True = pro, False =
    standard), possibly empty. Builds its own CampaignSnapshot; recovery.py
    uses it for the slices after a Recovery Dataset switch."""
    snapshot = CampaignSnapshot.take(cfg, session, get_condor_progress)
    plan = snapshot.slices_to_plan()
    if not dry_run:
        snapshot.record_stuck()
    return plan


def _manage_stragglers(cfg, snapshot, dry_run):
    try:
        stragglers.manage(cfg, snapshot, dry_run=dry_run)
    except OSError:
        logging.exception("straggler check failed -- continuing with cleanup and recovery")


def slice_dataset(cfg, slice_number):
    return cfg["input_dataset_template"].format(n=slice_number)


def submit_plan(cfg, session, plan, set_input_dataset=True):
    """Submit each planned slice in order, persisting last_split after each
    success. Shared with recovery.py; see docs/adr/0012. Unless
    set_input_dataset is False (recovery, whose Recovery Dataset must stay),
    the stage is first pointed at the slice's dataset (docs/adr/0022).
    Returns False if POMS reported the campaign stage exhausted partway
    through."""
    for use_pro in plan:
        if set_input_dataset:
            session.set_input_dataset(slice_dataset(cfg, cfg["last_split"]))
        session.set_subgroup(use_pro)
        submission_id = session.submit_next_slice()
        if submission_id is None:
            logging.info(
                "submit_next_slice returned no submission (campaign stage exhausted) "
                "-- stopping further slice submissions this run"
            )
            return False
        cfg["last_split"] += 1
        persist_last_split(cfg, cfg["last_split"])
    return True


def run(cfg, dry_run):
    import poms_client as pc

    session = PomsSession(pc, cfg)

    try:
        snapshot = CampaignSnapshot.take(cfg, session)
    except RuntimeError:
        # Non-2xx HTTP (e.g. expired token); skip this cycle, retry next hour.
        logging.exception("could not fetch POMS progress -- skipping this run")
        return
    plan = snapshot.slices_to_plan()
    if not dry_run:
        snapshot.record_stuck()
    if no_splits_left(cfg):
        _manage_stragglers(cfg, snapshot, dry_run)
    if not plan:
        if dry_run:
            if snapshot.cleanup_ready():
                logging.info("dry-run: would run duplicate-cleanup and turn switch off")
        elif snapshot.cleanup_ready():
            cleanup.run_cleanup(cfg, session)
        return

    if dry_run:
        subgroup_plan = ["pro" if use_pro else "standard" for use_pro in plan]
        datasets = [slice_dataset(cfg, cfg["last_split"] + i) for i in range(len(plan))]
        logging.info(
            "dry-run: would submit %d slice(s) with subgroup plan=%s against dataset(s)=%s",
            len(plan), subgroup_plan, datasets,
        )
        return

    # A launch changes what is in flight, so recovery looks at a fresh snapshot.
    if not submit_plan(cfg, session, plan) and CampaignSnapshot.take(cfg, session).recovery_ready():
        recovery.evaluate_and_run_recovery(cfg, session)


def run_test_launch(cfg, dry_run):
    """A Test Launch is outside the planning machinery: one launch_jobs call,
    no recovery, cleanup, straggler or last_split handling. The only State
    File write is PomsSession's submissions cache. The subgroup comes from
    the stage's test_param_overrides."""
    import poms_client as pc

    if dry_run:
        logging.info("dry-run: would submit 1 test launch")
        return
    if PomsSession(pc, cfg).submit_next_slice() is None:
        logging.info("test launch: POMS reports no more splits in this campaign stage")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=os.path.join(REPO_ROOT, "configs", "config.ini"))
    parser.add_argument("--dry-run", action="store_true", help="log decisions without updating params or submitting")
    parser.add_argument("--follow-launch", action="store_true", help="print POMS's launch stdout while waiting for the jobsub id")
    args = parser.parse_args()

    cfg = load_config(args.config)
    cfg["follow_launch"] = args.follow_launch

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
        if cfg["test_launch"]:
            try:
                run_test_launch(cfg, args.dry_run)
            except Exception:
                logging.exception("test launch failed")
                return 1
            return 0

        if not cfg["switch"]:
            logging.info("switch is off in %s, skipping this run", cfg["state_file"])
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
