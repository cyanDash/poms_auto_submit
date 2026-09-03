"""recovery: the seam to scripts/run_recovery.sh; see docs/adr/0010, 0011.
evaluate_and_run_recovery() is the module's one interface, same convention
as plan_next_slices().
"""

import logging
import os
import subprocess

from poms_session import ACTIVE_SUBMISSION_STATUSES

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RECOVERY_SCRIPT = os.path.join(SCRIPT_DIR, "run_recovery.sh")
RECOVERY_SCRIPT_TIMEOUT_SECONDS = 3600

NO_RECOVERY_NEEDED_MARKER = "NO_RECOVERY_NEEDED"

# See CONTEXT.md's Status entry. Anything terminal outside this set (Failed,
# Cancelled, Removed, LaunchFailed, Awaiting Approval, Approved) needs
# manual review, not auto-recovery.
RECOVERY_ELIGIBLE_STATUSES = {"Completed", "Located"}


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
    """Runs at most once per exhaustion event. Returns 'already_handled' |
    'waiting' | 'needs_manual_review' | 'recovery_script_failed' |
    'no_recovery_needed' | 'recovery_submitted' | 'recovery_submit_failed'."""
    from poms_auto_submit import persist_last_split, persist_recovery_handled, plan_next_slices, submit_plan

    if cfg.get("recovery_handled"):
        return "already_handled"

    submissions = session.get_progress()
    if not submissions:
        logging.info("recovery: no submission history yet -- waiting")
        return "waiting"

    last = submissions[-1]
    status = last.get("status")
    if status in ACTIVE_SUBMISSION_STATUSES:
        logging.info(
            "recovery: last slice (submission_id=%s) still %s (pct_complete=%s) -- waiting",
            last.get("submission_id"), status, last.get("pct_complete"),
        )
        return "waiting"
    if status not in RECOVERY_ELIGIBLE_STATUSES:
        logging.warning(
            "recovery: last slice (submission_id=%s) ended in status=%s -- needs manual review",
            last.get("submission_id"), status,
        )
        persist_recovery_handled(cfg["config_path"], True)
        cfg["recovery_handled"] = True
        return "needs_manual_review"

    stage = session.get_stage_params()
    input_dataset = stage["dataset"]
    output_defnames_path = os.path.join(
        cfg["cache_dir"], f"output_definitions_{session.campaign_stage_id}.txt"
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
