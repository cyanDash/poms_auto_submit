"""Read-only regression tests against a real, live POMS campaign stage.

Excluded from normal `pytest` runs (see configs/pytest.ini's `-m "not live"`
default) since these need POMS_CLIENT_DIR + a valid auth token and hit the
network. Run explicitly with: `source setup.sh && pytest -c configs/pytest.ini -m live`.

Makes no launch_jobs or param-update calls -- purely read APIs, safe to run
against production. This is PomsSession's own contract test: it pins real
poms_client.py response shapes down against the live server, so a fake `pc`
drifting from reality gets caught here instead of only in production.
"""
import pytest

from poms_client_bootstrap import setup_poms_client_path

try:
    setup_poms_client_path()
except RuntimeError as e:
    pytest.skip(str(e), allow_module_level=True)

import poms_client as pc  # noqa: E402
from poms_session import PomsSession  # noqa: E402

pytestmark = pytest.mark.live

EXPERIMENT = "sbnd"
ROLE = "production"
CAMPAIGN_ID = 11503  # test_poms_auto_submit_PDS_Detvar3_sdas1
# get_campaign_stage_name() is broken server-side (StagesPOMS.py returns a
# raw SQLAlchemy Row, which fails JSON serialization -> HTTP 400), so the
# stage name is hardcoded here instead of resolved from the id.
CAMPAIGN_STAGE_ID = 27002
CAMPAIGN_STAGE_NAME = "scrub_detsim_reco1_reco2_caf"
KNOWN_SUBMISSIONS = {
    3135073: "expected finished",
    3134969: "expected new/running",
}


@pytest.fixture(scope="module")
def session():
    # get_campaign_name() silently returns '' instead of the real name when
    # called without role= -- see docs/poms_client_gotchas.md.
    campaign_name = pc.get_campaign_name(EXPERIMENT, CAMPAIGN_ID, role=ROLE)
    cfg = {
        "experiment": EXPERIMENT,
        "role": ROLE,
        "campaign_name": campaign_name,
        "campaign_stage_name": CAMPAIGN_STAGE_NAME,
        "pct_complete_threshold": 80,
        "submit_two_slices": False,
    }
    return PomsSession(pc, cfg)


def test_campaign_stage_id_round_trips(session):
    # sanity check: does the id->name we're trusting round-trip back to the
    # same id via the lookup PomsSession actually depends on (name->id)?
    assert session.campaign_stage_id == CAMPAIGN_STAGE_ID


@pytest.mark.parametrize("submission_id", KNOWN_SUBMISSIONS)
def test_submission_details_shape(submission_id):
    ok, details = pc.submission_details(EXPERIMENT, ROLE, submission_id)
    assert ok
    assert "pct_complete" in details.get("submission", {})


def test_get_progress_returns_expected_shape(session):
    submissions = session.get_progress()
    for s in submissions:
        assert set(s) == {"submission_id", "status", "pct_complete", "jobsub_job_id"}


def test_get_stage_params_returns_named_stage(session):
    stage = session.get_stage_params()
    assert stage["name"] == CAMPAIGN_STAGE_NAME
    assert isinstance(stage.get("param_overrides"), list)
    for entry in stage["param_overrides"]:
        assert len(entry) == 2
