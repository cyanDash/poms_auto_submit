"""Read-only regression tests against a real, live POMS campaign stage.

Excluded from normal `pytest` runs (see pytest.ini's `-m "not live"`
default) since these need POMS_CLIENT_DIR + a valid auth token and hit the
network. Run explicitly with: `source setup.sh && pytest -m live`.

Makes no launch_jobs or param-update calls -- purely read APIs, safe to run
against production. This exists because the unit tests' hand-written fakes
have drifted from the real poms_client.py response shapes before (nested
response fields, an (ok, data) vs. bare-string return) without anything
catching it; these tests pin the real shapes down.
"""
import os
import sys

import pytest

poms_client_dir = os.environ.get("POMS_CLIENT_DIR")
if not poms_client_dir:
    pytest.skip(
        "POMS_CLIENT_DIR not set; source setup.sh before running live tests",
        allow_module_level=True,
    )
sys.path.insert(0, os.path.join(poms_client_dir, "python"))

import poms_client as pc  # noqa: E402
import poms_auto_submit as psc  # noqa: E402

pytestmark = pytest.mark.live

EXPERIMENT = "sbnd"
ROLE = "production"
CAMPAIGN_ID = 11206
# get_campaign_stage_name() is broken server-side (StagesPOMS.py returns a
# raw SQLAlchemy Row, which fails JSON serialization -> HTTP 400), so the
# stage name is hardcoded here instead of resolved from the id.
CAMPAIGN_STAGE_ID = 26646
CAMPAIGN_STAGE_NAME = "scrub_detsim_reco1_reco2_caf"
KNOWN_SUBMISSIONS = {
    3127741: "expected finished",
    3127787: "expected new/running",
}


@pytest.fixture(scope="module")
def cfg():
    pc.update_session_experiment(EXPERIMENT)
    pc.update_session_role(ROLE)
    campaign_name = pc.get_campaign_name(EXPERIMENT, CAMPAIGN_ID)
    return {
        "experiment": EXPERIMENT,
        "role": ROLE,
        "campaign_name": campaign_name,
        "campaign_stage_name": CAMPAIGN_STAGE_NAME,
        "pct_complete_threshold": 80,
        "submit_two_slices": False,
    }


def test_campaign_stage_id_round_trips(cfg):
    # sanity check: does the id->name we're trusting round-trip back to the
    # same id via the function poms_auto_submit.py actually depends on
    # (name->id)?
    resolved = pc.get_campaign_stage_id(
        cfg["experiment"], cfg["campaign_name"], cfg["campaign_stage_name"]
    )
    assert resolved == CAMPAIGN_STAGE_ID


@pytest.mark.parametrize("submission_id", KNOWN_SUBMISSIONS)
def test_submission_details_shape(submission_id):
    ok, details = pc.submission_details(EXPERIMENT, ROLE, submission_id)
    assert ok
    assert "pct_complete" in details.get("submission", {})


def test_get_progress_returns_expected_shape(cfg):
    progress = psc.get_progress(pc, cfg)
    assert set(progress) == {"campaign_stage_id", "submissions"}
    assert progress["campaign_stage_id"] == CAMPAIGN_STAGE_ID
    for s in progress["submissions"]:
        assert set(s) == {"submission_id", "status", "pct_complete"}


def test_get_stage_params_returns_named_stage(cfg):
    stage = psc.get_stage_params(pc, cfg)
    assert stage["name"] == CAMPAIGN_STAGE_NAME
    assert isinstance(stage.get("param_overrides"), list)
    for entry in stage["param_overrides"]:
        assert len(entry) == 2


def test_has_pro_subgroup_reads_real_param_overrides(cfg):
    stage = psc.get_stage_params(pc, cfg)
    # Just needs to not blow up on the real shape -- either bool is valid,
    # this isn't asserting a specific pro/standard state.
    assert psc.has_pro_subgroup(stage["param_overrides"]) in (True, False)
