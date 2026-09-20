"""_effective_pct_complete() is now only the cleanup gate's progress source
(_cleanup_ready); the in-flight decision reads condor_q directly, see
test_in_flight.py."""

from datetime import datetime, timedelta

import poms_auto_submit as psc
from helpers import make_cfg, sub

NOW = datetime(2026, 8, 30, 12, 0, 0)


def no_condor(experiment, jobsub_job_id):
    return None


def effective(s, get_condor_pct_complete):
    return psc._effective_pct_complete(make_cfg(), s, NOW, get_condor_pct_complete)


def test_condor_pct_complete_wins_over_raw_pct_complete():
    assert effective(sub(1, 90.0), lambda e, j: 10.0) == 10.0


def test_effective_pct_complete_is_rounded_to_two_decimal_places():
    assert effective(sub(1, 0.04), lambda e, j: 95.98000399920016) == 95.98


def test_progress_is_not_logged_when_past_99_percent(caplog):
    with caplog.at_level("INFO"):
        effective(sub(1, 0.04), lambda e, j: 99.98)
    assert "submission_id=1" not in caplog.text


def test_progress_is_logged_at_exactly_99_percent(caplog):
    with caplog.at_level("INFO"):
        effective(sub(1, 0.04), lambda e, j: 99.0)
    assert "pct_complete=99.0 " in caplog.text


def test_no_condor_and_no_pct_complete_is_none():
    assert effective(sub(1, None), no_condor) is None


def test_stale_submission_uses_statuses_array_proxy():
    s = sub(1, 0.04, last_status_change=NOW - timedelta(hours=3), files_submitted=10000, files_pending=299)
    assert round(effective(s, no_condor), 2) == 97.01


def test_recently_changed_submission_uses_raw_pct_complete():
    s = sub(1, 0.04, last_status_change=NOW - timedelta(hours=1), files_submitted=10000, files_pending=299)
    assert effective(s, no_condor) == 0.04


def test_stale_submission_without_file_counts_uses_raw_pct_complete():
    s = sub(1, 0.04, last_status_change=NOW - timedelta(hours=3))
    assert effective(s, no_condor) == 0.04
