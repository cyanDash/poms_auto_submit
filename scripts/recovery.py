"""recovery: the seam to scripts/run_recovery.sh; see docs/adr/0010, 0011.
evaluate_and_run_recovery() is the module's one interface, same convention
as plan_next_slices(); all eligibility/readiness checking lives in
poms_auto_submit.py's _recovery_ready(), which reads is_handled().
"""

import logging
import os
import subprocess

import state

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECOVERY_SCRIPT = os.path.join(SCRIPT_DIR, "run_recovery.sh")
RECOVERY_SCRIPT_TIMEOUT_SECONDS = 3600

NO_RECOVERY_NEEDED_MARKER = "NO_RECOVERY_NEEDED"


def is_handled(cfg):
    """Whether recovery already reached a conclusive outcome for this
    config; see docs/adr/0019."""
    return state.read(cfg, "recovery_handled", False) is True


def _mark_handled(cfg):
    state.write(cfg, "recovery_handled", True)


def _record_output_definitions(cfg, path):
    """Move run_recovery.sh's output defnames into the state file. A
    test-launch's are dropped: cleanup.py must never see a debug run's."""
    try:
        with open(path) as f:
            outputs = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return
    os.remove(path)
    if not cfg.get("test_launch"):
        state.write(cfg, "output_definitions", outputs)


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

def evaluate_and_run_recovery(cfg, session):
    """Runs at most once per campaign stage. Returns 'recovery_script_failed' |
    'no_recovery_needed' | 'recovery_plan_failed' | 'recovery_submitted' |
    'recovery_submit_failed'."""
    from poms_auto_submit import persist_last_split, plan_next_slices, submit_plan

    stage = session.get_stage_params()
    input_dataset = stage["dataset"]
    # Scratch file run_recovery.sh writes its output defnames to; its
    # prestage log is named after it, hence the test-launch suffix.
    suffix = "_test_launch" if cfg.get("test_launch") else ""
    output_defnames_path = os.path.join(
        cfg["cache_dir"], f"output_definitions_{session.campaign_stage_id}{suffix}.txt"
    )

    try:
        ratio, threshold, recovery_dataset = run_recovery_script(
            input_dataset, cfg["campaign_name"], output_defnames_path
        )
        _record_output_definitions(cfg, output_defnames_path)
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError):
        logging.exception("recovery: run_recovery.sh failed -- will retry next run")
        return "recovery_script_failed"

    if recovery_dataset is None:
        if ratio is None:
            logging.info("recovery: no recovery needed (input dataset has 0 files)")
        else:
            logging.info("recovery: no recovery needed (output/input ratio: %.2f > %.2f)", ratio, threshold)
        _mark_handled(cfg)
        return "no_recovery_needed"

    logging.info("recovery: needed (output/input ratio: %.2f <= %.2f) -- dataset=%s", ratio, threshold, recovery_dataset)

    session.set_recovery_input_dataset(recovery_dataset)
    persist_last_split(cfg, 0)
    cfg["last_split"] = 0

    try:
        plan = plan_next_slices(cfg, session)
    except RuntimeError:
        # Transient POMS hiccup right after the switch; POMS's Input Dataset
        # is already the recovery one, so the ordinary next-hour run picks
        # this up normally -- not a conclusive outcome, don't mark handled.
        logging.exception("recovery: could not fetch POMS progress after resetting input dataset")
        return "recovery_plan_failed"

    _mark_handled(cfg)

    if not submit_plan(cfg, session, plan):
        logging.error("recovery: submit_next_slice() returned None immediately after resetting cs_last_split")
        return "recovery_submit_failed"

    logging.info("recovery: submitted %d slice(s) against recovery dataset=%s", len(plan), recovery_dataset)
    return "recovery_submitted"
