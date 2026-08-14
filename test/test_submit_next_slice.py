import types

import pytest

import poms_auto_submit as psc
from helpers import make_cfg


def test_submit_next_slice_returns_submission_id_on_success():
    fake_pc = types.SimpleNamespace(
        launch_campaign_stage_jobs=lambda campaign_stage_id, experiment=None, role=None: (
            "some redirect data",
            303,
            555,
        ),
    )

    submission_id = psc.submit_next_slice(fake_pc, make_cfg(), campaign_stage_id=42)

    assert submission_id == 555


def test_submit_next_slice_raises_on_non_303_status():
    fake_pc = types.SimpleNamespace(
        launch_campaign_stage_jobs=lambda campaign_stage_id, experiment=None, role=None: (
            "some error",
            500,
            None,
        ),
    )

    with pytest.raises(RuntimeError):
        psc.submit_next_slice(fake_pc, make_cfg(), campaign_stage_id=42)
