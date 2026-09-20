"""Warn when a POMS-active Submission stays in one Status with no condor_q data."""

import json
import logging

import poms_auto_submit as psc
from condor_progress import Progress
from helpers import make_cfg

LIVE = Progress("live", 10.0)
NO_DATA = Progress("no_data")
ERROR = Progress("error")


def entry(submission_id, status):
    return {"submission_id": submission_id, "status": status, "subgroup": None, "jobsub_job_id": f"{submission_id}@s"}


def run(tmp_path, submissions, progress):
    cfg = make_cfg(cache_dir=str(tmp_path))
    return psc._in_flight_submissions(cfg, submissions, lambda e, j: progress[j], track_stuck=True)


def counts(tmp_path):
    with open(tmp_path / "stuck_no_data_test_stage.json") as f:
        return {k: v["runs"] for k, v in json.load(f).items()}


def stuck_warnings(caplog):
    return [r for r in caplog.records if r.levelname == "WARNING" and "manual intervention" in r.getMessage()]


def test_count_increments_across_runs_via_cache_file(tmp_path):
    for expected in (1, 2, 3):
        run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
        assert counts(tmp_path) == {"1": expected}


def test_warns_from_the_fifth_run_on_and_still_holds_slot(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(4):
            run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
        assert stuck_warnings(caplog) == []
        for n in (5, 6):
            in_flight = run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
            assert len(in_flight) == 1
            assert len(stuck_warnings(caplog)) == n - 4


def test_resets_on_status_change(tmp_path):
    run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
    run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
    run(tmp_path, [entry(1, "Running")], {"1@s": NO_DATA})
    assert counts(tmp_path) == {"1": 1}


def test_resets_when_condor_q_data_appears(tmp_path):
    run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
    run(tmp_path, [entry(1, "Held")], {"1@s": LIVE})
    assert counts(tmp_path) == {}


def test_resets_when_submission_leaves_active_set(tmp_path):
    run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
    run(tmp_path, [entry(1, "Failed")], {"1@s": NO_DATA})
    assert counts(tmp_path) == {}


def test_drops_submission_no_longer_in_window(tmp_path):
    run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
    run(tmp_path, [entry(2, "Held")], {"2@s": NO_DATA})
    assert counts(tmp_path) == {"2": 1}


def test_condor_q_error_leaves_counts_untouched(tmp_path):
    run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
    run(tmp_path, [entry(1, "Held"), entry(2, "Held")], {"1@s": NO_DATA, "2@s": ERROR})
    assert counts(tmp_path) == {"1": 1}


def test_no_cache_dir_is_a_silent_noop(caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(6):
            psc._in_flight_submissions(make_cfg(), [entry(1, "Held")], lambda e, j: NO_DATA)
    assert stuck_warnings(caplog) == []


def test_recovery_gate_does_not_advance_stuck_counts(tmp_path):
    cfg = make_cfg(cache_dir=str(tmp_path))
    subs = [{"submission_id": 1, "status": "Held", "jobsub_job_id": "1@s"}]
    no_data = lambda e, j: NO_DATA
    psc._any_still_running(cfg, subs, no_data)
    assert not list(tmp_path.glob("stuck_no_data_*.json"))


def test_malformed_cache_file_is_treated_as_empty(tmp_path):
    (tmp_path / "stuck_no_data_test_stage.json").write_text('{"1": "junk"}')
    run(tmp_path, [entry(1, "Held")], {"1@s": NO_DATA})
    assert counts(tmp_path) == {"1": 1}
