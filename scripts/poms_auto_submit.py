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
import time

import cleanup
import output_definitions
import recovery
import split_window
import sam_client
import state
import stragglers
from campaign_snapshot import PRO_ELIGIBLE_ROLE, CampaignSnapshot, no_splits_left
from poms_client_bootstrap import setup_poms_client_path
from poms_session import EmptyDataset, LaunchFailed, PomsSession

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

    cfg = {
        "experiment": parser.get("poms", "experiment"),
        "role": PRO_ELIGIBLE_ROLE,
        "campaign_name": campaign_name,
        "campaign_stage_name": parser.get("poms", "campaign_stage_name"),
        "pct_complete_threshold": parser.getfloat("decision", "pct_complete_threshold"),
        "submit_two_slices": parser.getboolean("decision", "submit_two_slices", fallback=False),
        "max_splits": parser.getint("decision", "max_splits"),
        "test_launch": parser.getboolean("decision", "test_launch", fallback=False),
        "do_recovery": parser.getboolean("decision", "do_recovery", fallback=False),
        "do_cleanup": parser.getboolean("decision", "do_cleanup", fallback=False),
        "log_file": os.path.join(campaign_dir, "poms_auto_submit.log"),
        "lock_file": os.path.join(campaign_dir, "poms_auto_submit.lock"),
    }
    cfg["split"] = split_window.read(parser)
    cfg["combine"] = split_window.read_combine(parser) if cfg["split"] else None
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


def record_window_launch(cfg, window_start, submission_id):
    """Remember which Submission took the input window starting at the unix
    time `window_start`, for whoever reads the State File later (the Run 3
    plotter): the `window_launches` section, keyed by that time."""
    launches = state.read(cfg, "window_launches", {}) or {}
    state.write(cfg, "window_launches", {**launches, str(window_start): {"submission_id": submission_id}})


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


def infer_output_definitions(cfg, session, dry_run):
    """Infers the Output Definitions once, when no splits are left, for
    recovery and cleanup to read; see docs/adr/0023. Does nothing unless
    do_recovery or do_cleanup is on or they are already recorded. A failure
    is logged and retried next run."""
    if not (cfg["do_recovery"] or cfg["do_cleanup"]):
        return
    if output_definitions.recorded_output_definitions(cfg):
        return
    try:
        sam = sam_client.get_sam_client(cfg["experiment"])
        output_definitions.get_output_definitions(cfg, session, sam, dry_run=dry_run)
    except Exception:
        # Any SAM, POMS or import failure: nothing was learned.
        logging.exception("output definitions: could not infer them -- will retry next run")


def _skip_empty_windows(cfg, sam, dataset, now):
    """Counts every launch whose window holds no input file in SAM, without
    launching it (docs/adr/0027). Returns False when the current window has
    not finished yet, so there is nothing to launch this run."""
    split = cfg["split"]
    while True:
        if split_window.window_end(split, cfg["last_split"]) > now:
            return False
        try:
            count = sam.countFiles(dimensions=split_window.window_dimensions(split, cfg["last_split"], dataset))
        except Exception:
            logging.warning("launch %d: could not count its input files in SAM -- launching anyway", cfg["last_split"], exc_info=True)
            return True
        if count:
            return True
        logging.info(
            "launch %d: no input files created %s in SAM -- counting it without launching",
            cfg["last_split"], split_window.describe(split, cfg["last_split"]),
        )
        cfg["last_split"] += 1
        persist_last_split(cfg, cfg["last_split"])


def _launch_combined(cfg, session, sam, dataset, now):
    """The one launch that takes the windows of launches first..last of the
    ini's [combine] section together (docs/adr/0028), as a pro slice.
    Returns 'wait' (its last window is still open), 'empty', 'failed' or
    'launched'. last_split moves past last_launch, and every window gets a
    window_launches entry with the one submission_id."""
    split, (first, last) = cfg["split"], cfg["combine"]
    if split_window.window_end(split, last) > now:
        logging.info("combined launch %d-%d: its last window ends later -- waiting", first, last)
        return "wait"
    dims = split_window.combined_dimensions(split, first, last, dataset)
    name = split_window.combined_name(split, first, last, dataset)
    span = f"{split_window.describe(split, first)} .. {split_window.describe(split, last)}"
    if not sam.countFiles(dimensions=dims):
        logging.info("combined launch %d-%d: no input files in %s -- counting them without launching", first, last, span)
        cfg["last_split"] = last + 1
        persist_last_split(cfg, cfg["last_split"])
        return "empty"
    try:
        sam.descDefinition(name)
    except Exception:
        sam.createDefinition(name, dims)
    logging.info("combined launch %d-%d takes input files created %s as %s", first, last, span, name)
    session.set_subgroup(True)
    session.set_input_dataset(name)
    session.set_split_type("")
    try:
        submission_id = session.submit_next_slice()
    except EmptyDataset:
        submission_id = "empty"
    except LaunchFailed as e:
        logging.error("%s -- not counted; stopping further slice submissions this run", e)
        return "failed"
    finally:
        # The next daily launch must take the real Input Dataset and window.
        session.set_input_dataset(dataset)
        session.set_split_type(split_window.build_split_type(split, last + 1))
    if submission_id is None:
        return "failed"
    if submission_id != "empty":
        for n in range(first, last + 1):
            record_window_launch(cfg, split_window.window_start(split, n), submission_id)
    cfg["last_split"] = last + 1
    persist_last_split(cfg, cfg["last_split"])
    return "launched" if submission_id != "empty" else "empty"


def submit_plan(cfg, session, plan, sam=None, dataset=None, now=None):
    """Submit each planned slice in order, persisting last_split after each
    success. Shared with recovery.py; see docs/adr/0012. With `sam` and
    `dataset` on a split campaign, windows without input files are counted
    without a launch. Returns False if POMS reported the campaign stage
    exhausted partway through."""
    now = time.time() if now is None else now
    for use_pro in plan:
        if cfg.get("combine") and cfg["last_split"] == cfg["combine"][0]:
            if sam is None or not dataset:
                logging.error("combined launch needs the stage's input dataset and SAM -- not launching")
                return True
            if _launch_combined(cfg, session, sam, dataset, now) in ("wait", "failed"):
                return True
            continue
        if cfg.get("split") and sam is not None and dataset:
            if not _skip_empty_windows(cfg, sam, dataset, now):
                return True
        session.set_subgroup(use_pro)
        if cfg.get("split"):
            split_type = split_window.build_split_type(cfg["split"], cfg["last_split"])
            logging.info(
                "launch %d takes input files created %s; split window: %s",
                cfg["last_split"], split_window.describe(cfg["split"], cfg["last_split"]), split_type,
            )
            session.set_split_type(split_type)
        try:
            submission_id = session.submit_next_slice()
        except EmptyDataset:
            window = f" created {split_window.describe(cfg['split'], cfg['last_split'])}" if cfg.get("split") else ""
            logging.info(
                "launch %d: no input files%s (POMS: empty dataset) -- counting it and moving on",
                cfg["last_split"], window,
            )
            cfg["last_split"] += 1
            persist_last_split(cfg, cfg["last_split"])
            continue
        except LaunchFailed as e:
            # Not counted as a slice: the next run retries this window.
            logging.error("%s -- not counted; stopping further slice submissions this run", e)
            return True
        if submission_id is None:
            logging.info(
                "submit_next_slice returned no submission (campaign stage exhausted) "
                "-- stopping further slice submissions this run"
            )
            return False
        if cfg.get("split"):
            record_window_launch(cfg, split_window.window_start(cfg["split"], cfg["last_split"]), submission_id)
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
        infer_output_definitions(cfg, session, dry_run)
        _manage_stragglers(cfg, snapshot, dry_run)
    if not plan:
        if snapshot.cleanup_ready():
            # A dry run writes the cleanup plan and retires nothing.
            cleanup.run_cleanup(cfg, session, dry_run=dry_run)
        return

    if dry_run:
        subgroup_plan = ["pro" if use_pro else "standard" for use_pro in plan]
        if cfg.get("split"):
            for n, subgroup in enumerate(subgroup_plan, start=cfg["last_split"]):
                if cfg.get("combine") and n == cfg["combine"][0]:
                    first, last = cfg["combine"]
                    logging.info(
                        "dry-run: would take launches %d-%d together (%s .. %s) as one pro slice",
                        first, last, split_window.describe(cfg["split"], first), split_window.describe(cfg["split"], last),
                    )
                    continue
                logging.info(
                    "dry-run: would take input files created %s with plan=%s",
                    split_window.describe(cfg["split"], n), subgroup,
                )
        else:
            logging.info("dry-run: would submit %d slice(s) with subgroup plan=%s", len(plan), subgroup_plan)
        return

    sam = dataset = None
    if cfg.get("split"):
        try:
            dataset = session.get_stage_params()["dataset"]
            sam = sam_client.get_sam_client(cfg["experiment"])
        except Exception:
            logging.warning("could not read the stage's input dataset -- not checking windows for files", exc_info=True)
    # A launch changes what is in flight, so recovery looks at a fresh snapshot.
    if not submit_plan(cfg, session, plan, sam=sam, dataset=dataset):
        # POMS ran out of splits before max_splits; infer before recovery
        # can switch the stage to the Recovery Dataset.
        infer_output_definitions(cfg, session, dry_run)
        if CampaignSnapshot.take(cfg, session).recovery_ready():
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
    try:
        if PomsSession(pc, cfg).submit_next_slice() is None:
            logging.info("test launch: POMS reports no more splits in this campaign stage")
    except LaunchFailed as e:
        logging.error("test launch: %s", e)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", default=os.path.join(REPO_ROOT, "configs", "config.ini"), help="path to the campaign ini (default: configs/config.ini)")
    parser.add_argument("--dry-run", action="store_true", help="log decisions without updating params, submitting or retiring; writes the cleanup plan when cleanup is due")
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
