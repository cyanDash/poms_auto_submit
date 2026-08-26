#!/usr/bin/env python
"""One-off manual verification: fires a real POMS test launch
(launch_jobs with test_launch=1) against the HNL campaign stage. POMS
launches using the stage's test_param_overrides instead of its regular
param_overrides -- for campaign_stage_id=26938 that's already set to a
single small job (-Osubmit.N=1, -Oglobal.productiontype=test_sdas1), so
this queues one real, cheap, test-tagged grid job. Not a pytest test since
it has a real side effect -- run it deliberately, once, when you want to
confirm the launch codepath actually works end-to-end before trusting
poms_auto_submit.py's automated production launches.

Calls make_poms_call() directly instead of poms_client.py's
launch_campaign_stage_jobs() wrapper: the wrapper assumes the response body
ends in "_<digits>" and does int(data[data.rfind("_") + 1:]) to extract the
submission id, but the real response for this call is a URL --
".../list_launch_file/sbnd/analysis?campaign_stage_id=26938&submission_id=NNN"
-- so rfind("_") lands inside "campaign_stage_id" and the int() conversion
crashes (confirmed live, 2026-08-14). Parse submission_id out of the URL's
query string instead.

Usage: source setup.sh && ./test/manual_test_launch.py
"""

import os
import sys
from urllib.parse import urlparse, parse_qs

poms_client_dir = os.environ.get("POMS_CLIENT_DIR")
if not poms_client_dir:
    sys.exit(
        "POMS_CLIENT_DIR is not set. Set up UPS and poms_client first:\n"
        "  source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh\n"
        "  setup poms_client"
    )
sys.path.insert(0, os.path.join(poms_client_dir, "python"))

import poms_client as pc

EXPERIMENT = "sbnd"
ROLE = "analysis"
CAMPAIGN_STAGE_ID = 26938


def main():
    pc.update_session_experiment(EXPERIMENT)
    pc.update_session_role(ROLE)

    data, status = pc.make_poms_call(
        method="launch_jobs",
        campaign_stage_id=CAMPAIGN_STAGE_ID,
        test_launch=1,
        experiment=EXPERIMENT,
        role=ROLE,
    )
    print(f"status: {status}")
    print(f"data: {data}")

    if status != 303:
        sys.exit(f"test launch failed: status={status} data={data}")

    submission_id = parse_qs(urlparse(data).query).get("submission_id", [None])[0]
    print(f"submission_id: {submission_id}")


if __name__ == "__main__":
    main()
