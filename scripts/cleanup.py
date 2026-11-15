"""cleanup: retires a finished Campaign Stage's duplicate output files and
turns the stage off. run_cleanup() is the module's one interface, same
convention as recovery.py's evaluate_and_run_recovery(); all
eligibility/readiness checking lives in campaign_snapshot.py's
CampaignSnapshot.cleanup_ready() -- by the time run_cleanup() is called, the campaign is
already known to be done.
"""

import logging

import duplicate_cleanup
import output_definitions
import sam_client

RETRY_NOTE = "switch stays on, will retry next run"


def run_cleanup(cfg, session, sam=None, dry_run=False):
    """Plans, retires and verifies the duplicates of the stage's Output
    Definitions, then turns the switch off in the State File. `sam` is the
    SAM client; the real one when None. Any failure leaves the switch on.
    A dry run writes the plan file and the lists of the full paths of the
    files it would retire; it records nothing in the State File and changes
    nothing in SAM. Returns True when the cleanup (or the dry run) went through."""
    from poms_auto_submit import persist_switch

    sam = sam_client.get_sam_client(cfg["experiment"], sam)

    try:
        definitions = output_definitions.get_output_definitions(cfg, session, sam, dry_run=dry_run)
    except Exception:
        # Any SAM, POMS or import failure: nothing was cleaned.
        logging.exception("cleanup: could not get the output definitions -- %s", RETRY_NOTE)
        return False

    result = duplicate_cleanup.clean_definitions(cfg, definitions, sam, dry_run=dry_run)

    if result["skipped"]:
        logging.info(
            "cleanup: %d file(s) left in place, has children outside the plan (listed in %s)",
            len(result["skipped"]), result["plan_path"],
        )
    if result["retired_files_lists"]:
        logging.info(
            "cleanup: full paths of the files to retire are listed in %s", ", ".join(result["retired_files_lists"]),
        )
    if result["bad_inputs"]:
        logging.error(
            "cleanup: verification failed for %d input(s): %s -- %s",
            len(result["bad_inputs"]), ", ".join(sorted(result["bad_inputs"])), RETRY_NOTE,
        )
        return False
    if result["failure"]:
        logging.error("cleanup: failed (%s) -- %s", result["failure"]["error"], RETRY_NOTE)
        return False
    if dry_run:
        logging.info("dry-run: plan written to %s -- nothing retired, switch left on", result["plan_path"])
        return True

    persist_switch(cfg, False)
    cfg["switch"] = False
    logging.info("cleanup: done and verified -- switch turned off")
    return True
