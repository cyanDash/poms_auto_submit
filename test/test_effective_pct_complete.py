from datetime import datetime, timedelta

import poms_auto_submit as psc
from helpers import make_cfg, make_submissions, sub

NOW = datetime(2026, 8, 30, 12, 0, 0)


def no_condor(experiment, jobsub_job_id):
    return None


# --- layer 1: condor_q wins whenever it returns a value ---

def test_condor_pct_complete_past_threshold_frees_slot():
    submissions = make_submissions(sub(1, 0.04))
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=lambda e, j: 97.0)
    assert num == 1


def test_condor_pct_complete_still_under_threshold_stays_in_flight():
    submissions = make_submissions(sub(1, 90.0))  # raw pct_complete would say "ready"
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=lambda e, j: 10.0)
    assert num == 0


def test_condor_pct_complete_past_threshold_frees_its_pro_slot():
    submissions = make_submissions(sub(1, 0.04, subgroup="pro"))
    in_flight = psc._in_flight_submissions(
        make_cfg(), submissions, now=NOW, get_condor_pct_complete=lambda e, j: 97.0
    )
    assert in_flight == []
    assert psc._pro_available(in_flight) is True


def test_effective_pct_complete_is_rounded_to_two_decimal_places(caplog):
    submissions = make_submissions(sub(1, 0.04))
    with caplog.at_level("INFO"):
        psc._in_flight_submissions(
            make_cfg(), submissions, now=NOW, get_condor_pct_complete=lambda e, j: 95.98000399920016
        )
    assert "pct_complete=95.98 " in caplog.text


# --- suppress log noise for submissions that are effectively done ---

def test_progress_is_not_logged_when_past_99_percent(caplog):
    submissions = make_submissions(sub(1, 0.04))
    with caplog.at_level("INFO"):
        psc._in_flight_submissions(make_cfg(), submissions, now=NOW, get_condor_pct_complete=lambda e, j: 99.98)
    assert "submission_id=1" not in caplog.text


def test_progress_is_logged_at_exactly_99_percent(caplog):
    submissions = make_submissions(sub(1, 0.04))
    with caplog.at_level("INFO"):
        psc._in_flight_submissions(make_cfg(), submissions, now=NOW, get_condor_pct_complete=lambda e, j: 99.0)
    assert "pct_complete=99.0 " in caplog.text


def test_condor_pct_complete_resolves_even_when_pct_complete_is_none():
    # A cache-hit submission (see docs/adr/0008-cache-static-submission-fields.md)
    # has jobsub_job_id but no pct_complete -- condor_q must still be tried,
    # not skipped in favor of an unconditional "still in-flight".
    submissions = make_submissions(sub(1, None, subgroup=None))
    submissions[0]["jobsub_job_id"] = "100@jobsub01.fnal.gov"
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=lambda e, j: 97.0)
    assert num == 1


def test_no_condor_and_no_pct_complete_stays_in_flight():
    # Neither signal is available at all -- conservative default, same as
    # the old pct_complete-is-None shortcut, just reached via a different path.
    submissions = make_submissions(sub(1, None, subgroup=None))
    submissions[0]["jobsub_job_id"] = "100@jobsub01.fnal.gov"
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=no_condor)
    assert num == 0


# --- layer 2: stale-status statuses-array proxy, only when condor_q is None ---

def test_stale_submission_past_threshold_by_proxy_frees_slot():
    # last_status_change is 3h stale (> STALE_STATUS_HOURS=2); condor_q is
    # unavailable; files_submitted/files_pending imply the real work is
    # past threshold -- the proxy should replace the stuck value.
    submissions = make_submissions(
        sub(1, 0.04, last_status_change=NOW - timedelta(hours=3), files_submitted=10000, files_pending=299)
    )
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=no_condor)
    assert num == 1


def test_stale_submission_still_under_threshold_by_proxy_stays_in_flight():
    submissions = make_submissions(
        sub(1, 0.04, last_status_change=NOW - timedelta(hours=3), files_submitted=100, files_pending=90)
    )
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=no_condor)
    assert num == 0


def test_recently_changed_low_pct_complete_submission_is_not_treated_as_stale():
    # last_status_change is only 1h old (< STALE_STATUS_HOURS=2) -- the
    # proxy must not be used yet even though file counts would imply
    # past-threshold progress; the raw pct_complete still governs.
    submissions = make_submissions(
        sub(1, 0.04, last_status_change=NOW - timedelta(hours=1), files_submitted=10000, files_pending=299)
    )
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=no_condor)
    assert num == 0


# --- layer 3: raw pct_complete, when neither condor_q nor the proxy apply ---

def test_stale_submission_without_file_counts_falls_back_to_raw_pct_complete():
    submissions = make_submissions(
        sub(1, 0.04, last_status_change=NOW - timedelta(hours=3), files_submitted=None, files_pending=None)
    )
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=no_condor)
    assert num == 0


def test_not_stale_and_no_condor_uses_raw_pct_complete():
    submissions = make_submissions(sub(1, 90.0))
    num = psc._next_slice_count(make_cfg(), submissions, now=NOW, get_condor_pct_complete=no_condor)
    assert num == 1
