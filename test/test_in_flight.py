"""In-flight decision from condor_q over a Submission window; see
docs/adr/0005 and the POMS-unreliability incident of 2026-09-17."""

import logging

import poms_auto_submit as psc
from condor_progress import Progress
from helpers import make_cfg

LIVE = Progress("live", 10.0, job_status="Held")
LIVE_PAST_THRESHOLD = Progress("live", 95.0)
FINISHED = Progress("finished", 100.0)
NO_DATA = Progress("no_data")
ERROR = Progress("error")


class FakeSession:
    def __init__(self, submissions):
        self._submissions = submissions

    def get_progress(self):
        return self._submissions


def entry(submission_id, status, subgroup=None, jobsub_job_id="1@jobsub01.fnal.gov"):
    return {"submission_id": submission_id, "status": status, "subgroup": subgroup, "jobsub_job_id": jobsub_job_id}


def plan(submissions, progress_by_id, **cfg_overrides):
    def lookup(experiment, jobsub_job_id):
        return progress_by_id[jobsub_job_id]

    return psc.plan_next_slices(make_cfg(**cfg_overrides), FakeSession(submissions), get_condor_progress=lookup)


def one(status, progress, **cfg_overrides):
    return plan([entry(1, status)], {"1@jobsub01.fnal.gov": progress}, **cfg_overrides)


def test_located_with_live_dag_counts_as_in_flight():
    assert one("Located", LIVE) == []


def test_completed_with_live_dag_counts_as_in_flight():
    assert one("Completed", LIVE) == []


def test_failed_with_live_dag_counts_as_in_flight():
    assert one("Failed", LIVE) == []


def test_live_dag_past_threshold_frees_the_slot():
    assert one("Running", LIVE_PAST_THRESHOLD) == [True]


def test_finished_dag_frees_the_slot_even_when_poms_says_running():
    assert one("Running", FINISHED) == [True]


def test_terminal_status_with_no_data_frees_the_slot():
    assert one("Located", NO_DATA) == [True]


def test_failed_with_no_data_frees_the_slot():
    assert one("Failed", NO_DATA) == [True]


def test_active_status_with_no_data_holds_the_slot():
    for status in ("New", "Idle", "Running", "Held"):
        assert one(status, NO_DATA) == [], status


def test_missing_jobsub_job_id_is_no_data_not_an_error():
    calls = []
    session = FakeSession([entry(1, "New", jobsub_job_id=None), entry(2, "Failed", jobsub_job_id=None)])

    result = psc.plan_next_slices(
        make_cfg(submit_two_slices=True), session, get_condor_progress=lambda e, j: calls.append(j),
    )

    assert calls == []
    assert result == [True]  # New holds one slot, Failed frees its own


def test_condor_q_error_holds_every_submission_in_the_window(caplog):
    submissions = [entry(1, "Located", jobsub_job_id="1@s"), entry(2, "Failed", jobsub_job_id="2@s")]
    progress = {"1@s": FINISHED, "2@s": ERROR}

    with caplog.at_level(logging.WARNING):
        result = plan(submissions, progress, submit_two_slices=True)

    assert result == []
    assert any(r.levelname == "WARNING" and "condor_q" in r.getMessage() for r in caplog.records)


def test_pro_slot_is_held_by_a_located_submission_with_live_dag():
    submissions = [entry(1, "Located", subgroup="pro", jobsub_job_id="1@s")]

    result = plan(submissions, {"1@s": LIVE}, submit_two_slices=True)

    assert result == [False]


def progress_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("progress:")]


def test_logs_only_live_submissions_without_poms_status_or_flags(caplog):
    with caplog.at_level(logging.INFO):
        one("Located", LIVE)
        one("Running", FINISHED)
        one("Held", NO_DATA)

    lines = progress_lines(caplog)
    assert len(lines) == 1
    assert "submission_id=1" in lines[0] and "completion_%=" in lines[0] and "status=Held" in lines[0]
    for banned in ("Located", "Running", "in_flight", "condor_q="):
        assert banned not in lines[0]


def test_no_disagreement_warning_between_poms_and_condor(caplog):
    with caplog.at_level(logging.WARNING):
        one("Located", LIVE)
        one("Running", FINISHED)

    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_no_warning_when_poms_and_condor_agree(caplog):
    with caplog.at_level(logging.WARNING):
        one("Running", LIVE)
        one("Completed", FINISHED)

    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_poms_pct_complete_of_100_with_live_dag_has_no_effect():
    s = entry(1, "Running")
    s["pct_complete"] = 100.0
    assert plan([s], {"1@jobsub01.fnal.gov": LIVE}) == []
