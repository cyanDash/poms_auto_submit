import types

import pytest

import poms_auto_submit as psc
from helpers import make_cfg


def test_get_stage_params_finds_named_stage():
    stages = [
        {"name": "other_stage", "dataset": "other_dataset"},
        {"name": "test_stage", "dataset": "target_dataset"},
    ]
    fake_pc = types.SimpleNamespace(
        show_campaign_stages=lambda campaign_name: (True, {"campaign_stages": stages}),
    )

    stage = psc.get_stage_params(fake_pc, make_cfg())

    assert stage == {"name": "test_stage", "dataset": "target_dataset"}


def test_get_stage_params_raises_when_stage_not_found():
    fake_pc = types.SimpleNamespace(
        show_campaign_stages=lambda campaign_name: (True, {"campaign_stages": [{"name": "other_stage"}]}),
    )

    with pytest.raises(RuntimeError):
        psc.get_stage_params(fake_pc, make_cfg())


def test_update_stage_params_is_noop_when_no_updates():
    calls = []
    fake_pc = types.SimpleNamespace(
        update_stage_param_overrides=lambda *a, **kw: calls.append((a, kw)),
    )

    psc.update_stage_params(fake_pc, make_cfg(), campaign_stage_id=42, updates={})

    assert calls == []


def test_update_stage_params_applies_param_overrides():
    calls = []

    def fake_update(experiment, campaign_stage, param_overrides=None):
        calls.append((experiment, campaign_stage, param_overrides))
        return True, "ok"

    fake_pc = types.SimpleNamespace(update_stage_param_overrides=fake_update)

    psc.update_stage_params(fake_pc, make_cfg(), campaign_stage_id=42, updates={"numjobs": "10"})

    assert calls == [("sbnd", 42, {"numjobs": "10"})]


def test_update_stage_params_raises_on_failure():
    fake_pc = types.SimpleNamespace(
        update_stage_param_overrides=lambda *a, **kw: (False, "server rejected update"),
    )

    with pytest.raises(RuntimeError):
        psc.update_stage_params(fake_pc, make_cfg(), campaign_stage_id=42, updates={"numjobs": "10"})
