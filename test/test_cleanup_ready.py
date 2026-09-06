from datetime import datetime

import poms_auto_submit as psc
from helpers import make_cfg, make_submissions, sub

NOW = datetime(2026, 8, 30, 12, 0, 0)


class FakeSession:
    def __init__(self, progress):
        self._progress = progress

    def get_progress(self):
        return self._progress


def ready_cfg(**overrides):
    cfg = make_cfg(do_cleanup=True, recovery_handled=True, last_split=5, max_splits=5)
    cfg.update(overrides)
    return cfg


def done_submission(pct_complete=99.0, status="Completed"):
    s = sub(1, pct_complete)
    s["status"] = status
    return s


# --- _no_splits_left ---

def test_no_splits_left_true_when_last_split_reaches_max():
    assert psc._no_splits_left(make_cfg(last_split=5, max_splits=5)) is True


def test_no_splits_left_false_when_splits_remain():
    assert psc._no_splits_left(make_cfg(last_split=3, max_splits=5)) is False


# --- _cleanup_ready ---

def test_cleanup_ready_true_when_everything_lines_up():
    session = FakeSession(make_submissions(done_submission()))
    assert psc._cleanup_ready(ready_cfg(), session, now=NOW, get_condor_pct_complete=lambda e, j: None) is True


def test_cleanup_ready_false_when_do_cleanup_off():
    session = FakeSession(make_submissions(done_submission()))
    cfg = ready_cfg(do_cleanup=False)
    assert psc._cleanup_ready(cfg, session, now=NOW, get_condor_pct_complete=lambda e, j: None) is False


def test_cleanup_ready_false_when_recovery_not_handled():
    session = FakeSession(make_submissions(done_submission()))
    cfg = ready_cfg(recovery_handled=False)
    assert psc._cleanup_ready(cfg, session, now=NOW, get_condor_pct_complete=lambda e, j: None) is False


def test_cleanup_ready_false_when_splits_remain():
    session = FakeSession(make_submissions(done_submission()))
    cfg = ready_cfg(last_split=3, max_splits=5)
    assert psc._cleanup_ready(cfg, session, now=NOW, get_condor_pct_complete=lambda e, j: None) is False


def test_cleanup_ready_false_when_no_submissions_yet():
    session = FakeSession([])
    assert psc._cleanup_ready(ready_cfg(), session, now=NOW, get_condor_pct_complete=lambda e, j: None) is False


def test_cleanup_ready_false_when_last_submission_failed():
    # A high-but-stale pct_complete on a Failed slice must not trigger
    # cleanup -- see docs/adr/0016-cleanup-gates-on-last-slice-completion.md.
    session = FakeSession(make_submissions(done_submission(pct_complete=99.0, status="Failed")))
    assert psc._cleanup_ready(ready_cfg(), session, now=NOW, get_condor_pct_complete=lambda e, j: None) is False


def test_cleanup_ready_false_when_pct_complete_at_threshold_but_not_past():
    session = FakeSession(make_submissions(done_submission(pct_complete=98.0)))
    assert psc._cleanup_ready(ready_cfg(), session, now=NOW, get_condor_pct_complete=lambda e, j: None) is False


def test_cleanup_ready_true_when_condor_reports_past_threshold():
    session = FakeSession(make_submissions(done_submission(pct_complete=None)))
    assert psc._cleanup_ready(ready_cfg(), session, now=NOW, get_condor_pct_complete=lambda e, j: 99.5) is True


def test_cleanup_ready_uses_last_submission_in_list():
    session = FakeSession(make_submissions(
        done_submission(pct_complete=99.0, status="Completed"),
        done_submission(pct_complete=10.0, status="Running"),
    ))
    assert psc._cleanup_ready(ready_cfg(), session, now=NOW, get_condor_pct_complete=lambda e, j: None) is False
