import types

import poms_slice_cron as psc
from helpers import make_cfg


def test_get_progress_with_no_submissions_returns_none_fields():
    fake_pc = types.SimpleNamespace(
        get_campaign_stage_id=lambda experiment, campaign_name, stage_name: 42,
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"campaign_name": campaign_name, "stage_name": stage_name, "data": {"submissions": []}},
        ),
    )

    progress = psc.get_progress(fake_pc, make_cfg())

    assert progress == {"campaign_stage_id": 42, "submissions": []}


def test_get_progress_picks_latest_submission_when_none_running():
    submissions = [
        {"submission_id": 100, "status": "Located"},
        {"submission_id": 102, "status": "Finished"},
        {"submission_id": 101, "status": "Located"},
    ]
    details_by_id = {
        102: {"submission_id": "102", "submission": {"pct_complete": 100.0}},
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
        "submissions": [{"submission_id": 102, "status": "Finished", "pct_complete": 100.0}],
    }


def test_get_progress_returns_all_running_submissions():
    submissions = [
        {"submission_id": 100, "status": "Running"},
        {"submission_id": 102, "status": "Located"},
        {"submission_id": 101, "status": "Running"},
    ]
    details_by_id = {
        100: {"submission_id": "100", "submission": {"pct_complete": 10.0}},
        101: {"submission_id": "101", "submission": {"pct_complete": 55.0}},
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
        "submissions": [
            {"submission_id": 100, "status": "Running", "pct_complete": 10.0},
            {"submission_id": 101, "status": "Running", "pct_complete": 55.0},
        ],
    }
