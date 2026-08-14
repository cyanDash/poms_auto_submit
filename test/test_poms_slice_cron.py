import types

import pytest

import poms_slice_cron as psc


def make_cfg(**overrides):
    cfg = {
        "experiment": "sbnd",
        "role": "production",
        "campaign_name": "test_campaign",
        "campaign_stage_name": "test_stage",
        "pct_complete_threshold": 80,
    }
    cfg.update(overrides)
    return cfg


def test_get_progress_with_no_submissions_returns_none_fields():
    fake_pc = types.SimpleNamespace(
        get_campaign_stage_id=lambda experiment, campaign_name, stage_name: 42,
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"campaign_name": campaign_name, "stage_name": stage_name, "data": {"submissions": []}},
        ),
    )

    progress = psc.get_progress(fake_pc, make_cfg())

    assert progress == {
        "campaign_stage_id": 42,
        "submission_id": None,
        "pct_complete": None,
        "status": None,
    }


def test_get_progress_picks_latest_submission():
    submissions = [
        {"submission_id": 100, "status": "Located"},
        {"submission_id": 102, "status": "Running"},
        {"submission_id": 101, "status": "Located"},
    ]
    details_by_id = {
        102: {"submission_id": "102", "submission": {"pct_complete": 40.0}},
    }
    fake_pc = types.SimpleNamespace(
        get_campaign_stage_id=lambda experiment, campaign_name, stage_name: 42,
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (
            True,
            details_by_id[submission_id],
        ),
    )

    progress = psc.get_progress(fake_pc, make_cfg())

    assert progress == {
        "campaign_stage_id": 42,
        "submission_id": 102,
        "pct_complete": 40.0,
        "status": "Running",
    }


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
