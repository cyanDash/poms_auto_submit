"""recovery: the seam to scripts/run_recovery.sh; see docs/adr/0010, 0011.
evaluate_and_run_recovery() is the module's one interface, same convention
as plan_next_slices().
"""

import logging
import os
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECOVERY_SCRIPT = os.path.join(SCRIPT_DIR, "run_recovery.sh")
RECOVERY_SCRIPT_TIMEOUT_SECONDS = 3600

NO_RECOVERY_NEEDED_MARKER = "NO_RECOVERY_NEEDED"

def run_recovery_script(input_dataset, campaign_name, output_defnames_path):
    """Returns (ratio, threshold, dataset_name); ratio/dataset_name are None
    when not applicable. Raises RuntimeError on any other failure."""
    result = subprocess.run(
        [RECOVERY_SCRIPT, input_dataset, campaign_name, output_defnames_path],
        capture_output=True, text=True, timeout=RECOVERY_SCRIPT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"run_recovery.sh failed: exit {result.returncode}\n{result.stderr}")
    ratio_line, threshold_line, outcome_line = result.stdout.strip().splitlines()
    ratio = None if ratio_line == "N/A" else float(ratio_line)
    threshold = float(threshold_line)
    dataset_name = None if outcome_line == NO_RECOVERY_NEEDED_MARKER else outcome_line
    return ratio, threshold, dataset_name

def evaluate_and_run_recovery(cfg, session, get_condor_progress=None):
    """Runs at most once per exhaustion event. Returns 'already_handled' |
    'waiting' | 'recovery_script_failed' |
    'no_recovery_needed' | 'recovery_submitted' | 'recovery_submit_failed'."""
    from poms_auto_submit import (
        _any_still_running, persist_last_split, persist_recovery_handled, plan_next_slices, submit_plan,
    )

    if cfg.get("recovery_handled"):
        return "already_handled"

    submissions = session.get_progress()
    if not submissions:
        logging.info("recovery: no submission history yet -- waiting")
        return "waiting"

    if _any_still_running(cfg, submissions, get_condor_progress):
        logging.info("recovery: a submission is still running per condor_q -- waiting")
        return "waiting"

    stage = session.get_stage_params()
    input_dataset = stage["dataset"]
    # A test-launch's output defnames go to a sibling file, never the real
    # one -- cleanup.py's reader (and its "load only once" dedup) must never
    # see output from a debug run. run_recovery.sh never reads this path
    # back for its own dimension-building, so redirecting it here is safe.
    suffix = "_test_launch" if cfg.get("test_launch") else ""
    output_defnames_path = os.path.join(
        cfg["cache_dir"], f"output_definitions_{session.campaign_stage_id}{suffix}.txt"
    )

    try:
        ratio, threshold, recovery_dataset = run_recovery_script(
            input_dataset, cfg["campaign_name"], output_defnames_path
        )
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError):
        logging.exception("recovery: run_recovery.sh failed -- will retry next run")
        return "recovery_script_failed"

    if recovery_dataset is None:
        if ratio is None:
            logging.info("recovery: no recovery needed (input dataset has 0 files)")
        else:
            logging.info("recovery: no recovery needed (output/input ratio: %.2f > %.2f)", ratio, threshold)
        persist_recovery_handled(cfg["config_path"], True)
        cfg["recovery_handled"] = True
        return "no_recovery_needed"

    logging.info("recovery: needed (output/input ratio: %.2f <= %.2f) -- dataset=%s", ratio, threshold, recovery_dataset)

    session.set_recovery_input_dataset(recovery_dataset)
    persist_last_split(cfg["config_path"], 0)
    cfg["last_split"] = 0

    try:
        plan = plan_next_slices(cfg, session)
    except RuntimeError:
        # Transient POMS hiccup right after the switch; POMS's Input Dataset
        # is already the recovery one, so the ordinary next-hour run picks
        # this up normally -- not a conclusive outcome, don't persist.
        logging.exception("recovery: could not fetch POMS progress after resetting input dataset")
        return "recovery_plan_failed"

    persist_recovery_handled(cfg["config_path"], True)
    cfg["recovery_handled"] = True

    if not submit_plan(cfg, session, plan):
        logging.error("recovery: submit_next_slice() returned None immediately after resetting cs_last_split")
        return "recovery_submit_failed"

    logging.info("recovery: submitted %d slice(s) against recovery dataset=%s", len(plan), recovery_dataset)
    return "recovery_submitted"
