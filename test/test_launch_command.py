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
        if submission_id not in details_by_id:
            return False, {}
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
    assert session.get_launch_source() == {"command": "real", "submission_id": 1, "is_test": False, "windows": 1}

    only_test, _ = make_session(
        [{"submission_id": 2}], {2: {"command_executed": "test", "submission_params": {"test": 1}}},
    )
    assert only_test.get_launch_source() == {"command": "test", "submission_id": 2, "is_test": True, "windows": 1}
    assert make_session([], {})[0].get_launch_source() is None


def test_submission_source_reads_the_command_and_input_dataset_of_one_submission():
    session, calls = make_session([], {3144257: REAL_DETAILS["submission"]})

    source = session.get_submission_source(3144257)

    assert source["submission_id"] == 3144257
    assert "--mdappversion v10_14_02_0602" in source["command"]
    assert source["dataset"] == REAL_DETAILS["submission"]["campaign_stage_snapshot_obj"]["dataset"]
    assert source["is_test"] is False
    assert calls == [3144257]


def test_submission_source_is_none_when_poms_has_no_command_for_it():
    session, _ = make_session([], {7: {"command_executed": None}})

    assert session.get_submission_source(7) is None
    assert make_session([], {}, ok=False)[0].get_submission_source(7) is None


def make_paged_session(pages, details_by_id):
    """pages: {tmax or None: (submissions, prevlink)}; the call without tmax
    is the newest window."""
    requested = []

    def campaign_stage_submissions(experiment, role, campaign_name, stage_name, **kwargs):
        requested.append(kwargs)
        submissions, prevlink = pages[kwargs.get("tmax")]
        return True, {"data": {"submissions": submissions, "prevlink": prevlink}}

    pc = types.SimpleNamespace(
        update_session_experiment=lambda experiment: None,
        update_session_role=lambda role: None,
        campaign_stage_submissions=campaign_stage_submissions,
        submission_details=lambda experiment, role, submission_id: (
            (True, {"submission": details_by_id[submission_id]}) if submission_id in details_by_id else (False, {})
        ),
    )
    return PomsSession(pc, make_cfg()), requested


def prev(tmax, tdays=18):
    return f"/poms/campaign_stage_submissions/sbnd/production?campaign_name=c&tmax={tmax.replace(' ', '+')}&tdays={tdays}"


REAL_COMMAND = {"command_executed": "real%20cmd", "submission_params": {}}
TEST_COMMAND = {"command_executed": "test", "submission_params": {"test": 1}}


def test_launch_source_pages_back_to_an_older_window_when_the_newest_has_none():
    session, requested = make_paged_session(
        {
            None: ([{"submission_id": 9}], prev("2026-08-12 11:58:37")),
            "2026-08-12 11:58:37": ([{"submission_id": 4}], prev("2026-07-25 00:00:00", 19)),
        },
        {9: TEST_COMMAND, 4: REAL_COMMAND},
    )

    source = session.get_launch_source()

    assert source == {"command": "real cmd", "submission_id": 4, "is_test": False, "windows": 2}
    assert requested == [{}, {"tmax": "2026-08-12 11:58:37", "tdays": "18"}]


def test_launch_source_reads_one_window_when_it_holds_a_real_submission():
    session, requested = make_paged_session(
        {None: ([{"submission_id": 9}], prev("2026-08-12 11:58:37"))}, {9: REAL_COMMAND},
    )

    assert session.get_launch_source()["windows"] == 1
    assert requested == [{}]


def test_launch_source_falls_back_to_the_newest_test_launch_after_the_last_window():
    session, requested = make_paged_session(
        {
            None: ([{"submission_id": 9}], prev("2026-08-12 11:58:37")),
            "2026-08-12 11:58:37": ([{"submission_id": 4}], None),
        },
        {9: TEST_COMMAND, 4: {"command_executed": "older_test", "submission_params": {"test": 1}}},
    )

    assert session.get_launch_source() == {"command": "test", "submission_id": 9, "is_test": True, "windows": 2}


def test_launch_source_stops_after_twelve_windows():
    pages = {None: ([], prev("2026-01-30 00:00:00"))}
    for n in range(30, 0, -1):
        pages[f"2026-01-{n:02d} 00:00:00"] = ([], prev(f"2026-01-{n - 1:02d} 00:00:00") if n > 1 else None)
    session, requested = make_paged_session(pages, {})

    assert session.get_launch_source() is None
    assert len(requested) == 12
