#!/usr/bin/env python
"""One-off manual verification of PomsSession.set_recovery_input_dataset()
(see docs/poms_client_gotchas.md's "update_campaign_stage -- UNVERIFIED").
Writes to a real campaign stage -- use a disposable TEST stage, not
test_live_campaign.py's shared fixture (campaign_stage_id=27002).

Usage: source setup.sh && ./test/manual_test_update_campaign_stage.py <campaign_name> <campaign_stage_name> <test_dataset_name>
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from poms_client_bootstrap import setup_poms_client_path

try:
    setup_poms_client_path()
except RuntimeError as e:
    sys.exit(str(e))

import poms_client as pc
from poms_raw_client import raw_poms_call

EXPERIMENT = "sbnd"
ROLE = "production"


def stage_row(campaign_name, campaign_stage_name):
    ok, resp = pc.show_campaign_stages(campaign_name=campaign_name)
    if not ok:
        return None
    for stage in resp.get("campaign_stages", []):
        if stage.get("name") == campaign_stage_name:
            return stage
    return None


def main():
    if len(sys.argv) != 4:
        sys.exit(f"usage: {sys.argv[0]} <campaign_name> <campaign_stage_name> <test_dataset_name>")
    campaign_name, campaign_stage_name, dataset_name = sys.argv[1], sys.argv[2], sys.argv[3]

    pc.update_session_experiment(EXPERIMENT)
    pc.update_session_role(ROLE)

    campaign_stage_id = pc.get_campaign_stage_id(EXPERIMENT, campaign_name, campaign_stage_name)
    print(f"campaign_stage_id: {campaign_stage_id}")
    print(f"before: {stage_row(campaign_name, campaign_stage_name)}")

    data, status = raw_poms_call(
        pc, "update_campaign_stage",
        pcl_call=1, campaign_stage=campaign_stage_id,
        experiment=EXPERIMENT, role=ROLE,
        dataset=dataset_name, cs_last_split=0,
    )
    print(f"status: {status}")
    print(f"data: {data}")

    print(f"after: {stage_row(campaign_name, campaign_stage_name)}")


if __name__ == "__main__":
    main()
