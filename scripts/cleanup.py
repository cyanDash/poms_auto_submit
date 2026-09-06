"""cleanup: the seam to scripts/duplicate_cleanup.sh -- TODO.md's File
Cleanup feature. run_cleanup() is the module's one interface, same
convention as recovery.py's evaluate_and_run_recovery(); all
eligibility/readiness checking lives in poms_auto_submit.py's
_cleanup_ready() -- by the time run_cleanup() is called, the campaign is
already known to be done.
"""

import logging
import os
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLEANUP_SCRIPT = os.path.join(SCRIPT_DIR, "duplicate_cleanup.sh")
CLEANUP_SCRIPT_TIMEOUT_SECONDS = 3600


def _load_output_definitions(cache_dir, campaign_stage_id):
    """Unique output dataset names recovery.py has ever recorded for this
    campaign stage, in first-seen order. No recovery has ever run (or it
    never got past run_recovery.sh's dimension-building step) -> []."""
    path = os.path.join(cache_dir, f"output_definitions_{campaign_stage_id}.txt")
    try:
        with open(path) as f:
            lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []

    seen = set()
    unique = []
    for name in lines:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def run_cleanup(cfg, session):
    """Submits duplicate-cleanup for every unique output dataset recovery
    ever recorded, then turns the campaign stage's switch off -- meant to
    run exactly once, right after the campaign (including its recovery
    slices) has fully finished. Returns True if cleanup ran (or there was
    nothing to clean), False if duplicate_cleanup.sh failed."""
    from poms_auto_submit import persist_switch

    outputs = _load_output_definitions(cfg["cache_dir"], session.campaign_stage_id)
    if not outputs:
        logging.info("cleanup: no output definitions recorded -- nothing to clean")
    else:
        try:
            subprocess.run(
                [CLEANUP_SCRIPT, cfg["cache_dir"], *outputs],
                check=True, capture_output=True, text=True, timeout=CLEANUP_SCRIPT_TIMEOUT_SECONDS,
            )
        except (subprocess.SubprocessError, OSError):
            logging.exception("cleanup: duplicate_cleanup.sh failed -- will retry next run")
            return False
        logging.info("cleanup: submitted duplicate-cleanup for %d dataset(s)", len(outputs))

    persist_switch(cfg["config_path"], False)
    cfg["switch"] = False
    logging.info("cleanup: done -- switch turned off")
    return True
