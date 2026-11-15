#!/usr/bin/env python3
"""Recovers the time window of the newest real Submission of a `new()`-split
Campaign Stage, once per window, as a Test Launch; see
docs/adr/0025-recover-each-window-as-a-test-launch.md. Run by its own cron
line some hours after the daily launch. Never changes last_split."""

import argparse
import fcntl
import logging
import os
import re
import sys

import output_definitions
import recovery
import sam_client
import state
from poms_auto_submit import acquire_lock, load_config
from poms_client_bootstrap import setup_poms_client_path
from poms_session import LaunchFailed, PomsSession

SECTION = "window_recovery"
# Recover when any input of the window lacks an output (ratio below 100%).
RATIO_THRESHOLD = 1
MEMORY_KEY = "-Osubmit.memory="
RECOVERY_MEMORY = "16GB"
SUBGROUP_KEY = "-Osubmit.subgroup="
# Test overrides allowed to differ from param_overrides: they change where
# and how big the jobs run, never what the outputs are named.
FREE_TEST_KEYS = {SUBGROUP_KEY, MEMORY_KEY}
# command_executed is the jobsub_submit line; the fife_launch line has the
# same name as -Oglobal.sam_dataset=.
WINDOW_DATASET = re.compile(r"(?:--dataset_definition=|-Oglobal\.sam_dataset=)['\"]?([^\s'\"]+)")


def window_dataset(command):
    """The SAM definition POMS made for the launch's window
    (`<input>_<uuid>_time_<first>__<last>`), from its command."""
    match = WINDOW_DATASET.search(command or "")
    return match.group(1) if match else None


def test_launch_problems(stage):
    """Why a Test Launch of this stage would not be a real recovery: a test
    split type that would cut the Recovery Dataset, or a test override that
    changes the outputs' names. Empty when it is safe."""
    problems = []
    if (stage.get("test_split_type") or "").strip() not in ("", "None"):
        problems.append(f"test_split_type is {stage['test_split_type']!r}, not None")
    real = dict(stage.get("param_overrides") or [])
    for key, value in stage.get("test_param_overrides") or []:
        if key not in FREE_TEST_KEYS and real.get(key) != value:
            problems.append(f"test override {key}{value} differs from param_overrides ({real.get(key)!r})")
    return problems


def recover_window(cfg, session, test_session, sam, dry_run=False):
    """Returns 'no_window' | 'already_handled' | 'output_definitions_failed' |
    'unsafe_test_overrides' | 'no_recovery_needed' | 'dry_run' |
    'recovery_submitted' | 'recovery_failed'. `test_session` launches with
    test_launch set."""
    source = session.get_launch_source()
    window = window_dataset(source["command"]) if source and not source["is_test"] else None
    if window is None:
        logging.error("window recovery: no real Submission with a window dataset in its command")
        return "no_window"
    handled = state.read(cfg, SECTION, {}) or {}
    if window in handled:
        logging.info("window recovery: %s already handled: %s", window, handled[window])
        return "already_handled"
    logging.info("window recovery: window %s (Submission %s)", window, source["submission_id"])

    try:
        definitions = output_definitions.get_output_definitions(cfg, session, sam, dry_run=dry_run)
    except Exception:
        logging.exception("window recovery: could not get the output definitions -- will retry next run")
        return "output_definitions_failed"
    names = [d["name"] for d in definitions if d["depth"] == 1]

    stage = session.get_stage_params()
    problems = test_launch_problems(stage)
    if problems:
        logging.error("window recovery: not launched, fix the stage first: %s", "; ".join(problems))

    if dry_run:
        clause = " and ".join(f"isparentof: (defname: {n})" for n in names)
        missing = sam.countFiles(dimensions=f"defname: {window} and not ({clause})")
        logging.info(
            "dry-run: would recover %d file(s) of %s missing an output in %s, as a Test Launch with %s%s",
            missing, window, names, MEMORY_KEY, RECOVERY_MEMORY,
        )
        return "unsafe_test_overrides" if problems else "dry_run"
    if problems:
        return "unsafe_test_overrides"

    prestage_log = os.path.join(cfg["cache_dir"], f"window_recovery_{session.campaign_stage_id}_prestage.log")
    try:
        ratio, threshold, recovery_dataset = recovery.run_recovery_script(
            window, cfg["campaign_name"], prestage_log, names, ratio_threshold=RATIO_THRESHOLD,
        )
    except Exception:
        logging.exception("window recovery: run_recovery.sh failed -- will retry next run")
        return "recovery_failed"
    if recovery_dataset is None:
        logging.info("window recovery: none needed for %s (output/input ratio %s)", window, ratio)
        state.write(cfg, SECTION, {**handled, window: {"recovery_dataset": None}})
        return "no_recovery_needed"

    original_dataset = stage["dataset"]
    logging.info("window recovery: %s (output/input ratio %.4f) -- launching", recovery_dataset, ratio)
    try:
        test_session.update_test_stage_params({MEMORY_KEY: RECOVERY_MEMORY})
        session.set_input_dataset(recovery_dataset)
        submission_id = test_session.submit_next_slice()
    except (LaunchFailed, RuntimeError):
        logging.exception("window recovery: launch of %s failed", recovery_dataset)
        return "recovery_failed"
    finally:
        # The next daily launch must take the real Input Dataset.
        session.set_input_dataset(original_dataset)
    if submission_id is None:
        logging.error("window recovery: POMS launched nothing for %s", recovery_dataset)
        return "recovery_failed"
    state.write(cfg, SECTION, {**handled, window: {"recovery_dataset": recovery_dataset, "submission_id": submission_id}})
    logging.info("window recovery: submitted %s against %s", submission_id, recovery_dataset)
    return "recovery_submitted"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", "-c", required=True, help="path to the campaign ini")
    parser.add_argument("--dry-run", action="store_true", help="log what would be recovered; no SAM or POMS write")
    args = parser.parse_args()

    cfg = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(cfg["log_file"]), logging.StreamHandler()],
    )
    logging.info("===== window_recovery run start =====")
    try:
        if not cfg["switch"]:
            logging.info("switch is off in %s, skipping this run", cfg["state_file"])
            return 0
        lock_fh = acquire_lock(cfg["lock_file"])
        if lock_fh is None:
            logging.info("previous run still active (lock held), skipping this run")
            return 0
        try:
            setup_poms_client_path()
            import poms_client as pc

            session = PomsSession(pc, cfg)
            test_session = PomsSession(pc, {**cfg, "test_launch": True})
            sam = sam_client.get_sam_client(cfg["experiment"])
            outcome = recover_window(cfg, session, test_session, sam, dry_run=args.dry_run)
            return 1 if outcome in ("no_window", "output_definitions_failed", "unsafe_test_overrides", "recovery_failed") else 0
        except Exception:
            logging.exception("window recovery run failed")
            return 1
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()
    finally:
        logging.info("===== window_recovery run end =====")


if __name__ == "__main__":
    sys.exit(main())
