import types

import pytest

import poms_auto_submit as psc
from helpers import make_cfg

# Real response shape for a launch_jobs call (confirmed live, 2026-08-14):
# a redirect URL with submission_id in the query string, not "..._<digits>".
REAL_LAUNCH_JOBS_URL = (
    "https://pomsgpvm02.fnal.gov:9443/poms/list_launch_file/sbnd/analysis"
    "?campaign_stage_id=26938&submission_id=555"
)


def test_submit_next_slice_returns_submission_id_on_success():
    fake_pc = types.SimpleNamespace(
        make_poms_call=lambda **kw: (REAL_LAUNCH_JOBS_URL, 303),
    )

    submission_id = psc.submit_next_slice(fake_pc, make_cfg(), campaign_stage_id=42)

    assert submission_id == "555"


def test_submit_next_slice_raises_on_non_303_status():
    fake_pc = types.SimpleNamespace(
        make_poms_call=lambda **kw: ("some error", 500),
    )

    with pytest.raises(RuntimeError):
        psc.submit_next_slice(fake_pc, make_cfg(), campaign_stage_id=42)
