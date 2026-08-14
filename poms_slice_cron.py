#!/usr/bin/env python
"""Hourly cron entry point: check progress, decide whether to submit the next
slice of a POMS campaign stage, update stage params if needed, and submit.

Intended to run from crontab, e.g.:
    0 * * * * /path/to/poms_slice_cron.py --config /path/to/config.ini >> /path/to/cron.out 2>&1
"""

import argparse
import configparser
import fcntl
import logging
import os
import sys
import types

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


def check_auth(pc, cfg):
    """Best-effort warning if the proxy/token uploaded to POMS looks stale."""
    options = types.SimpleNamespace(
        test=None, experiment=cfg["experiment"], verbose=False
    )
    try:
        if pc.auth_token():
            stale = pc.check_stale_token(options)
        else:
            stale = pc.check_stale_proxy(options)
        if stale:
            logging.warning("POMS auth (proxy/token) looks stale — renew before relying on this run")
    except Exception:
        logging.exception("could not check auth staleness, continuing anyway")


def get_progress(pc, cfg):
    """Status/pct_complete of the submission(s) that matter right now: all
    still-Running submissions if any are running, otherwise just the latest.
    """
    campaign_stage_id = pc.get_campaign_stage_id(
        cfg["experiment"], cfg["campaign_name"], cfg["campaign_stage_name"]
    )

    ok, resp = pc.campaign_stage_submissions(
        cfg["experiment"], cfg["role"], cfg["campaign_name"], cfg["campaign_stage_name"],
    )
    submissions = resp.get("data", {}).get("submissions", []) if ok else []
    if not submissions:
        logging.info("no submissions found yet for %s/%s", cfg["campaign_name"], cfg["campaign_stage_name"])
        return {"campaign_stage_id": campaign_stage_id, "submissions": []}

    submissions = sorted(submissions, key=lambda s: s.get("submission_id", 0))
    running = [s for s in submissions if s.get("status") == "Running"]
    target = running if running else [submissions[-1]]

    result = []
    for s in target:
        submission_id = s.get("submission_id")
        ok, details = pc.submission_details(cfg["experiment"], cfg["role"], submission_id)
        pct_complete = details.get("submission", {}).get("pct_complete") if ok else None
        entry = {"submission_id": submission_id, "status": s.get("status"), "pct_complete": pct_complete}
        logging.info(
            "progress: campaign_stage_id=%s submission_id=%s status=%s pct_complete=%s",
            campaign_stage_id, submission_id, entry["status"], pct_complete,
        )
        result.append(entry)

    return {"campaign_stage_id": campaign_stage_id, "submissions": result}


def get_active_submission_count(pc, cfg, campaign_stage_id):
    """How many submissions for this campaign stage are still New/Idle/Running.

    running_submissions isn't wrapped in poms_client.py, so this goes through
    make_poms_call directly.
    """
    campaign_id = pc.get_campaign_id(cfg["experiment"], cfg["campaign_name"])
    data, status = pc.make_poms_call(
        method="running_submissions",
        fmt="json",
        campaign_id_list=str(campaign_id),
        experiment=cfg["experiment"],
        role=cfg["role"],
    )
    if status not in (200, 201):
        logging.warning("running_submissions call failed with status %s, assuming active", status)
        return None

    import json
    counts = json.loads(data)
    active = counts.get(str(campaign_id), 0) if isinstance(counts, dict) else 0
    logging.info("active_submission_count=%s", active)
    return active


def can_submit_next_slice(cfg, progress, active_count):
    """How many new slices to submit this run (0, 1, or 2).

    Target pipeline depth is 2 if submit_two_slices is set, else 1. A Running
    submission counts as "ready" once its pct_complete crosses
    pct_complete_threshold. Submitting tops the pipeline back up to target,
    minus however many currently-Running submissions haven't reached the
    threshold yet (those still occupy a slot).
    """
    target = 2 if cfg["submit_two_slices"] else 1

    if active_count is None:
        logging.info("decision: skip (could not determine active submission count)")
        return 0

    if active_count == 0:
        logging.info("decision: bootstrap (no active submissions), submit %d slice(s)", target)
        return target

    running = progress["submissions"]
    ready_count = sum(
        1 for s in running
        if s["pct_complete"] is not None and s["pct_complete"] > cfg["pct_complete_threshold"]
    )
    if ready_count == 0:
        logging.info("decision: skip (no running submission past pct_complete_threshold)")
        return 0

    not_ready_count = len(running) - ready_count
    num_slices = max(0, target - not_ready_count)
    logging.info(
        "decision: submit %d slice(s) (ready=%d not_ready=%d target=%d)",
        num_slices, ready_count, not_ready_count, target,
    )
    return num_slices


def get_stage_params(pc, cfg):
    """Block 3 (read): current params for the target campaign stage."""
    ok, resp = pc.show_campaign_stages(campaign_name=cfg["campaign_name"])
    if not ok:
        raise RuntimeError("show_campaign_stages failed")
    for stage in resp.get("campaign_stages", []):
        if stage.get("name") == cfg["campaign_stage_name"]:
            return stage
    raise RuntimeError(f"stage {cfg['campaign_stage_name']!r} not found in campaign {cfg['campaign_name']!r}")


def update_stage_params(pc, cfg, campaign_stage_id, updates):
    """Block 3 (write): apply param changes if any were decided on above.

    `updates` is a dict of param_overrides key/value pairs. No-op if empty.
    """
    if not updates:
        logging.info("no stage param updates to apply")
        return

    logging.info("updating stage params: %s", updates)
    ok, data = pc.update_stage_param_overrides(
        cfg["experiment"], campaign_stage_id, param_overrides=updates
    )
    if not ok:
        raise RuntimeError(f"update_stage_param_overrides failed: {data}")


def submit_next_slice(pc, cfg, campaign_stage_id):
    """Block 4: launch a new submission for the campaign stage."""
    data, status, submission_id = pc.launch_campaign_stage_jobs(
        campaign_stage_id, experiment=cfg["experiment"], role=cfg["role"]
    )
    if status != 303:
        raise RuntimeError(f"launch_campaign_stage_jobs failed: status={status} data={data}")
    logging.info("submitted new slice: submission_id=%s", submission_id)
    return submission_id


def run(cfg, dry_run):
    import poms_client as pc

    pc.update_session_experiment(cfg["experiment"])
    pc.update_session_role(cfg["role"])
    check_auth(pc, cfg)

    progress = get_progress(pc, cfg)
    campaign_stage_id = progress["campaign_stage_id"]
    active_count = get_active_submission_count(pc, cfg, campaign_stage_id)

    num_slices = can_submit_next_slice(cfg, progress, active_count)
    if num_slices == 0:
        return

    # TODO(user): decide what, if anything, needs to change before the next
    # slice(s) go out and populate this dict accordingly.
    updates = {}

    if dry_run:
        logging.info("dry-run: would apply updates=%s and submit %d slice(s)", updates, num_slices)
        return

    update_stage_params(pc, cfg, campaign_stage_id, updates)
    for _ in range(num_slices):
        submit_next_slice(pc, cfg, campaign_stage_id)


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
        logging.exception("poms_slice_cron run failed")
        return 1
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
