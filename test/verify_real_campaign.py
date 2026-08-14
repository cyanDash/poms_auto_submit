#!/usr/bin/env python
"""Read-only sanity check of poms_auto_submit's block-1/block-3 logic against a
real campaign. Makes no launch_jobs or param-update calls -- safe to run
against production.

Usage: ./verify_real_campaign.py
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)

poms_client_dir = os.environ.get("POMS_CLIENT_DIR")
if not poms_client_dir:
    sys.exit(
        "POMS_CLIENT_DIR is not set. Set up UPS and poms_client first:\n"
        "  source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh\n"
        "  setup poms_client"
    )
sys.path.insert(0, os.path.join(poms_client_dir, "python"))
sys.path.insert(0, PARENT_DIR)

import poms_client as pc
import poms_auto_submit as psc

EXPERIMENT = "sbnd"
ROLE = "production"
CAMPAIGN_ID = 11206
CAMPAIGN_STAGE_ID = 26646
# get_campaign_stage_name() is broken server-side (StagesPOMS.py:85-96 returns a
# raw SQLAlchemy Row, which fails JSON serialization -> HTTP 400), so the stage
# name is hardcoded here instead of resolved from the id.
CAMPAIGN_STAGE_NAME = "scrub_detsim_reco1_reco2_caf"
KNOWN_SUBMISSIONS = {
    3127741: "expected finished",
    3127787: "expected new/running",
}


def main():
    pc.update_session_experiment(EXPERIMENT)
    pc.update_session_role(ROLE)

    campaign_name = pc.get_campaign_name(EXPERIMENT, CAMPAIGN_ID)
    stage_name = CAMPAIGN_STAGE_NAME
    print(f"campaign_name={campaign_name!r} stage_name={stage_name!r}")

    # sanity check: does the id->name we're trusting round-trip back to the same id
    # via the function poms_auto_submit.py actually depends on (name->id)?
    resolved_stage_id = pc.get_campaign_stage_id(EXPERIMENT, campaign_name, stage_name)
    print(
        f"get_campaign_stage_id({campaign_name!r}, {stage_name!r}) = {resolved_stage_id} "
        f"(expected {CAMPAIGN_STAGE_ID}, match={resolved_stage_id == CAMPAIGN_STAGE_ID})"
    )

    print("\n-- submission_details() for known submissions --")
    for sub_id, expected in KNOWN_SUBMISSIONS.items():
        ok, details = pc.submission_details(EXPERIMENT, ROLE, sub_id)
        pct_complete = details.get("submission", {}).get("pct_complete") if ok else None
        print(f"submission {sub_id} ({expected}): ok={ok} pct_complete={pct_complete}")

    cfg = {
        "experiment": EXPERIMENT,
        "role": ROLE,
        "campaign_name": campaign_name,
        "campaign_stage_name": stage_name,
        "pct_complete_threshold": 80,
    }

    print("\n-- psc.get_progress() (block 1) --")
    progress = psc.get_progress(pc, cfg)
    print(progress)

    print("\n-- psc.get_stage_params() (block 3, read only) --")
    stage = psc.get_stage_params(pc, cfg)
    print(json.dumps(stage, indent=2))


if __name__ == "__main__":
    main()
