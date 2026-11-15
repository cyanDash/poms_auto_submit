import json
import types
from pathlib import Path

from helpers import make_cfg
from poms_session import PomsSession

RAW = Path(__file__).resolve().parent.parent / "docs" / "raw"
REAL_DETAILS = json.loads((RAW / "submission_details_3144257.json").read_text())


def make_session(submissions, details_by_id, ok=True):
    """submissions: campaign_stage_submissions() entries; details_by_id:
    {submission_id: `submission` object of submission_details()}."""
    calls = []

    def submission_details(experiment, role, submission_id):
        calls.append(submission_id)
        return True, {"submission": details_by_id[submission_id]}

    pc = types.SimpleNamespace(
        update_session_experiment=lambda experiment: None,
        update_session_role=lambda role: None,
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            ok, {"data": {"submissions": submissions}},
        ),
        submission_details=submission_details,
    )
    return PomsSession(pc, make_cfg()), calls


def test_launch_command_is_the_real_command_url_unquoted():
    session, _ = make_session([{"submission_id": 3144257}], {3144257: REAL_DETAILS["submission"]})

    command = session.get_launch_command()

    assert "export MT_PRODUCTIONNAME=test_PDS_detvar_Sample3 && export MT_PRODUCTIONTYPE=mc" in command
    assert "--mdappversion v10_14_02_0602" in command
    assert "%20" not in command


def test_launch_command_comes_from_the_newest_submission():
    session, calls = make_session(
        [{"submission_id": 1}, {"submission_id": 3}, {"submission_id": 2}],
        {n: {"command_executed": f"cmd%20{n}", "submission_params": {}} for n in (1, 2, 3)},
    )

    assert session.get_launch_command() == "cmd 3"
    assert calls == [3]


def test_launch_command_skips_submissions_without_one_and_test_launches():
    session, _ = make_session(
        [{"submission_id": n} for n in (1, 2, 3, 4)],
        {
            1: {"command_executed": "real", "submission_params": {}},
            2: {"command_executed": "also_real", "submission_params": {"dataset": "x"}},
            3: {"command_executed": "test", "submission_params": {"test": 1}},
            4: {"command_executed": None, "submission_params": None},
        },
    )

    assert session.get_launch_command() == "also_real"


def test_launch_command_of_a_test_launch_is_used_when_there_is_no_other():
    session, _ = make_session(
        [{"submission_id": 1}], {1: {"command_executed": "test", "submission_params": {"test": 1}}},
    )

    assert session.get_launch_command() == "test"


def test_launch_command_is_none_without_submissions():
    assert make_session([], {})[0].get_launch_command() is None
    assert make_session([{"submission_id": 1}], {}, ok=False)[0].get_launch_command() is None


def test_launch_source_names_the_submission_and_whether_it_is_a_test_launch():
    session, _ = make_session(
        [{"submission_id": n} for n in (1, 2)],
        {
            1: {"command_executed": "real", "submission_params": {}},
            2: {"command_executed": "test", "submission_params": {"test": 1}},
        },
    )
    assert session.get_launch_source() == {"command": "real", "submission_id": 1, "is_test": False}

    only_test, _ = make_session(
        [{"submission_id": 2}], {2: {"command_executed": "test", "submission_params": {"test": 1}}},
    )
    assert only_test.get_launch_source() == {"command": "test", "submission_id": 2, "is_test": True}
    assert make_session([], {})[0].get_launch_source() is None
