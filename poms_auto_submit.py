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
        "switch": parser.getboolean("decision", "switch", fallback=True),
        "pct_complete_threshold": parser.getfloat("decision", "pct_complete_threshold"),
        "submit_two_slices": parser.getboolean("decision", "submit_two_slices", fallback=False),
        "max_splits": parser.getint("decision", "max_splits"),
        "last_split": parser.getint("decision", "last_split"),
        "log_file": os.path.join(os.path.dirname(path), parser.get("paths", "log_file")),
        "lock_file": os.path.join(os.path.dirname(path), parser.get("paths", "lock_file")),
        "config_path": os.path.abspath(path),
    }
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


def next_slice_count(cfg, submissions):
    """Decide how many new slices to submit this run (0, 1, or 2)."""
    remaining_splits = cfg["max_splits"] - cfg["last_split"]
    if remaining_splits <= 0:
        logging.info(
            "decision: skip (max_splits reached: last_split=%d max_splits=%d)",
            cfg["last_split"], cfg["max_splits"],
        )
        return 0

    target = min(2 if cfg["submit_two_slices"] else 1, remaining_splits)

    if not submissions:
        logging.info("decision: No active submissions: submit %d slice(s)", target)
        return target

    ready_count = sum(
        1 for s in submissions
        if s["pct_complete"] is not None and s["pct_complete"] >= cfg["pct_complete_threshold"]
    )
    if ready_count == 0:
        logging.info("decision: skip (no active submission past pct_complete_threshold)")
        return 0

    not_ready_count = len(submissions) - ready_count
    num_slices = max(0, target - not_ready_count)
    logging.info(
        "decision: submit %d slice(s) (ready=%d not_ready=%d target=%d)",
        num_slices, ready_count, not_ready_count, target,
    )
    return num_slices


PRO_ELIGIBLE_ROLE = "production"


def plan_subgroups(num_slices, role):
    """Decide which subgroup each of the num_slices new submissions should use
    (see docs/adr/0002-lone-slice-defaults-to-pro-subgroup.md)."""
    if role != PRO_ELIGIBLE_ROLE:
        return [False] * num_slices
    if num_slices == 2:
        return [True, False]
    return [True]


def plan_next_slices(cfg, session):
    """Decide how many new slices to submit this run and which subgroup each gets.

    Returns a list with one entry per slice to submit (True = pro subgroup,
    False = standard), possibly empty.
    """
    submissions = session.get_progress()

    num_slices = next_slice_count(cfg, submissions)
    if num_slices == 0:
        return []

    return plan_subgroups(num_slices, cfg["role"])


def run(cfg, dry_run):
    import poms_client as pc

    session = PomsSession(pc, cfg)
    session.check_auth()

    plan = plan_next_slices(cfg, session)
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
    parser.add_argument("--config", default=os.path.join(SCRIPT_DIR, "config.ini"))
    parser.add_argument("--dry-run", action="store_true", help="log decisions without updating params or submitting")
    args = parser.parse_args()

    cfg = load_config(args.config)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(cfg["log_file"]), logging.StreamHandler()],
    )

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


if __name__ == "__main__":
    sys.exit(main())
