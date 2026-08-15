#!/usr/bin/env python
"""Hourly cron entry point: check progress, decide whether to submit the next
slice of a POMS campaign stage, update stage params if needed, and submit.

Intended to run from crontab, e.g.:
    0 * * * * /path/to/poms_auto_submit.py --config /path/to/config.ini >> /path/to/cron.out 2>&1
"""

import argparse
import configparser
import fcntl
import logging
import os
import sys

from poms_session import PomsSession

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise FileNotFoundError(f"could not read config file: {path}")

    poms_client_dir = os.environ.get("POMS_CLIENT_DIR")
    if not poms_client_dir:
        raise RuntimeError(
            "POMS_CLIENT_DIR is not set. Set up UPS and poms_client first:\n"
            "  source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh\n"
            "  setup poms_client"
        )
    sys.path.insert(0, os.path.join(poms_client_dir, "python"))

    cfg = {
        "experiment": parser.get("poms", "experiment"),
        "role": parser.get("poms", "role"),
        "campaign_name": parser.get("poms", "campaign_name"),
        "campaign_stage_name": parser.get("poms", "campaign_stage_name"),
        "pct_complete_threshold": parser.getfloat("decision", "pct_complete_threshold"),
        "submit_two_slices": parser.getboolean("decision", "submit_two_slices", fallback=False),
        "log_file": os.path.join(os.path.dirname(path), parser.get("paths", "log_file")),
        "lock_file": os.path.join(os.path.dirname(path), parser.get("paths", "lock_file")),
    }
    return cfg


def acquire_lock(lock_path):
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return None
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()
    return lock_fh


def can_submit_next_slice(cfg, submissions, active_count):
    """Decide how many new slices to submit this run (0, 1, or 2)."""
    target = 2 if cfg["submit_two_slices"] else 1

    if active_count is None:
        logging.info("decision: skip (could not determine active submission count)")
        return 0

    if active_count == 0:
        logging.info("decision: bootstrap (no active submissions), submit %d slice(s)", target)
        return target

    ready_count = sum(
        1 for s in submissions
        if s["pct_complete"] is not None and s["pct_complete"] > cfg["pct_complete_threshold"]
    )
    if ready_count == 0:
        logging.info("decision: skip (no running submission past pct_complete_threshold)")
        return 0

    not_ready_count = len(submissions) - ready_count
    num_slices = max(0, target - not_ready_count)
    logging.info(
        "decision: submit %d slice(s) (ready=%d not_ready=%d target=%d)",
        num_slices, ready_count, not_ready_count, target,
    )
    return num_slices


SUBGROUP_OVERRIDE_KEY = "-Osubmit.subgroup="
PRO_SUBGROUP = "pro"
PRO_ELIGIBLE_ROLE = "production"


def has_pro_subgroup(param_overrides):
    """Whether a stage's param_overrides currently sets subgroup=pro."""
    return any(
        k == SUBGROUP_OVERRIDE_KEY and v == PRO_SUBGROUP
        for k, v in param_overrides
    )


def plan_subgroups(num_slices, pro_in_use, role):
    """Decide which subgroup each of the num_slices new submissions should use."""
    if role != PRO_ELIGIBLE_ROLE:
        return [False] * num_slices
    if num_slices == 2:
        return [True, False]
    return [not pro_in_use]


def run(cfg, dry_run):
    import poms_client as pc

    session = PomsSession(pc, cfg)
    session.check_auth()

    submissions = session.get_progress()
    active_count = session.get_active_submission_count()

    num_slices = can_submit_next_slice(cfg, submissions, active_count)
    if num_slices == 0:
        return

    stage_params = session.get_stage_params()
    pro_in_use = has_pro_subgroup(stage_params.get("param_overrides", []))
    want_pro = plan_subgroups(num_slices, pro_in_use, cfg["role"])

    if dry_run:
        plan = ["pro" if p else "standard" for p in want_pro]
        logging.info(
            "dry-run: would submit %d slice(s) with subgroup plan=%s (pro_in_use=%s)",
            num_slices, plan, pro_in_use,
        )
        return

    for use_pro in want_pro:
        updates = {SUBGROUP_OVERRIDE_KEY: PRO_SUBGROUP if use_pro else ""}
        session.update_stage_params(updates)
        session.submit_next_slice()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.path.join(SCRIPT_DIR, "config.ini"))
    parser.add_argument("--dry-run", action="store_true", help="log decisions without updating params or submitting")
    args = parser.parse_args()

    cfg = load_config(args.config)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(cfg["log_file"]), logging.StreamHandler()],
    )

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


if __name__ == "__main__":
    sys.exit(main())
