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

from poms_client_bootstrap import setup_poms_client_path
from poms_session import PRO_SUBGROUP, PomsSession

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

# Fixed at the repo root, not configurable via config.ini -- every run must
# contend for the same lock file regardless of which config it's using.
LOCK_FILE = os.path.join(REPO_ROOT, "poms_auto_submit.lock")

# Not configurable via config.ini: setup.sh only ever authenticates via
# sbndpro's production-role managed-token credkey, so this is the only role
# any POMS call from this script could succeed with anyway.
PRO_ELIGIBLE_ROLE = "production"


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


def in_flight_submissions(cfg, submissions):
    """Active submissions still under pct_complete_threshold -- i.e. occupying
    a slot this run hasn't freed up yet (see CONTEXT.md's Status entry and
    docs/adr/0005-in-flight-slot-based-decision.md)."""
    threshold = cfg["pct_complete_threshold"]
    return [s for s in submissions if s["pct_complete"] is None or s["pct_complete"] < threshold]


def next_slice_count(cfg, submissions):
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
    in_flight = in_flight_submissions(cfg, submissions)
    num_slices = max(0, target - len(in_flight))
    logging.info(
        "decision: submit %d slice(s) (in_flight=%d target=%d)",
        num_slices, len(in_flight), target,
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


def plan_next_slices(cfg, session):
    """Decide how many new slices to submit this run and which subgroup each gets.

    Returns a list with one entry per slice to submit (True = pro subgroup,
    False = standard), possibly empty.
    """
    submissions = session.get_progress()

    num_slices = next_slice_count(cfg, submissions)
    if num_slices == 0:
        return []

    in_flight = in_flight_submissions(cfg, submissions)
    return plan_subgroups(num_slices, cfg["role"], pro_available(in_flight))


def run(cfg, dry_run):
    import poms_client as pc

    session = PomsSession(pc, cfg)
    session.check_auth()

    try:
        plan = plan_next_slices(cfg, session)
    except RuntimeError:
        # poms_client raises RuntimeError on non-2xx HTTP (e.g. a proxy/token that
        # expired between the stale-auth warning above and this call, HTTP 403).
        # Treat as a skip for this cycle rather than a hard failure -- the next
        # hourly run will pick up cleanly once the token is renewed.
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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(cfg["log_file"]), logging.StreamHandler()],
    )

    if not cfg["switch"]:
        logging.info("switch is off (switch=0 in config), skipping this run")
        return 0

    lock_fh = acquire_lock(LOCK_FILE)
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
