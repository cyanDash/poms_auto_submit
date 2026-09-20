import condor_progress
import poms_auto_submit as psc
from helpers import make_cfg


class FakeSession:
    def __init__(self, progress):
        self._progress = progress

    def get_progress(self):
        return self._progress


def ready_cfg(**overrides):
    cfg = make_cfg(do_cleanup=True, recovery_handled=True, last_split=5, max_splits=5)
    cfg.update(overrides)
    return cfg


# --- _no_splits_left ---

def test_no_splits_left_true_when_last_split_reaches_max():
    assert psc._no_splits_left(make_cfg(last_split=5, max_splits=5)) is True


def test_no_splits_left_false_when_splits_remain():
    assert psc._no_splits_left(make_cfg(last_split=3, max_splits=5)) is False


# --- _cleanup_ready ---

def _cp(outcome, pct=None):
    return condor_progress.Progress(outcome, pct)


def _job_sub(submission_id, status="Completed"):
    return {"submission_id": submission_id, "status": status, "jobsub_job_id": f"{submission_id}@s"}


def _by_job(outcomes):
    return lambda experiment, jobsub_job_id: outcomes[jobsub_job_id]


def _ready(cfg, subs, outcomes):
    return psc._cleanup_ready(cfg, FakeSession(subs), get_condor_progress=_by_job(outcomes))


def test_cleanup_ready_true_when_all_finished():
    subs = [_job_sub(1), _job_sub(2)]
    assert _ready(ready_cfg(), subs, {"1@s": _cp("finished", 100.0), "2@s": _cp("finished", 100.0)}) is True


def test_cleanup_ready_false_when_do_cleanup_off():
    assert _ready(ready_cfg(do_cleanup=False), [_job_sub(1)], {"1@s": _cp("finished", 100.0)}) is False


def test_cleanup_ready_false_when_recovery_not_handled():
    assert _ready(ready_cfg(recovery_handled=False), [_job_sub(1)], {"1@s": _cp("finished", 100.0)}) is False


def test_cleanup_ready_false_when_splits_remain():
    assert _ready(ready_cfg(last_split=3), [_job_sub(1)], {"1@s": _cp("finished", 100.0)}) is False


def test_cleanup_ready_false_when_no_submissions_yet():
    assert _ready(ready_cfg(), [], {}) is False


def test_cleanup_ready_true_despite_false_failed_when_condor_finished():
    assert _ready(ready_cfg(), [_job_sub(1, "Failed")], {"1@s": _cp("finished", 100.0)}) is True


def test_cleanup_ready_false_when_live_dag_below_threshold():
    assert _ready(ready_cfg(), [_job_sub(1, "Located")], {"1@s": _cp("live", 97.9)}) is False


def test_cleanup_ready_true_when_live_dag_past_threshold():
    assert _ready(ready_cfg(), [_job_sub(1)], {"1@s": _cp("live", 99.5)}) is True


def test_cleanup_ready_false_when_earlier_slice_still_live():
    subs = [_job_sub(1, "Running"), _job_sub(2)]
    assert _ready(ready_cfg(), subs, {"1@s": _cp("live", 50.0), "2@s": _cp("finished", 100.0)}) is False


def test_cleanup_ready_false_when_active_status_has_no_data():
    assert _ready(ready_cfg(), [_job_sub(1, "Held")], {"1@s": _cp("no_data")}) is False


def test_cleanup_ready_true_when_terminal_status_has_no_data():
    assert _ready(ready_cfg(), [_job_sub(1, "Completed")], {"1@s": _cp("no_data")}) is True


def test_cleanup_ready_false_on_condor_q_error():
    assert _ready(ready_cfg(), [_job_sub(1)], {"1@s": _cp("error")}) is False
