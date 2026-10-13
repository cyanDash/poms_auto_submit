import logging

import condor_progress
import poms_auto_submit as psc
import state
from helpers import make_cfg


class FakeSession:
    campaign_stage_id = 42

    def __init__(self, progress):
        self._progress = progress

    def get_progress(self):
        return self._progress


def ready_cfg(**overrides):
    cfg = make_cfg(do_cleanup=True, do_recovery=False, last_split=5, max_splits=5)
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


def test_cleanup_ready_false_when_recovery_not_handled(tmp_path):
    cfg = ready_cfg(do_recovery=True, cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"))
    assert _ready(cfg, [_job_sub(1)], {"1@s": _cp("finished", 100.0)}) is False


def test_cleanup_ready_true_when_recovery_handled(tmp_path):
    cfg = ready_cfg(do_recovery=True, cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"))
    (tmp_path / "state.json").write_text('{"recovery_handled": true}')
    assert _ready(cfg, [_job_sub(1)], {"1@s": _cp("finished", 100.0)}) is True


def test_cleanup_ready_false_when_splits_remain():
    assert _ready(ready_cfg(last_split=3), [_job_sub(1)], {"1@s": _cp("finished", 100.0)}) is False


def test_cleanup_ready_false_when_no_submissions_yet():
    assert _ready(ready_cfg(), [], {}) is False


def test_cleanup_ready_true_despite_false_failed_when_condor_finished():
    assert _ready(ready_cfg(), [_job_sub(1, "Failed")], {"1@s": _cp("finished", 100.0)}) is True


def test_cleanup_ready_false_when_live_dag_below_threshold():
    assert _ready(ready_cfg(), [_job_sub(1, "Located")], {"1@s": _cp("live", 97.9)}) is False


def test_cleanup_ready_true_when_live_dag_exactly_at_threshold():
    assert _ready(ready_cfg(), [_job_sub(1)], {"1@s": _cp("live", 98.0)}) is True


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


def _recovery_not_done_lines(caplog):
    return [r for r in caplog.records if "recovery: not done" in r.getMessage()]


def test_max_splits_reached_logs_recovery_not_done_when_unhandled(tmp_path, caplog):
    cfg = make_cfg(do_recovery=True, last_split=5, max_splits=5, state_file=str(tmp_path / "state.json"))
    with caplog.at_level(logging.INFO):
        assert psc._plan(cfg, [], None) == (0, [])
    assert len(_recovery_not_done_lines(caplog)) == 1


def test_max_splits_reached_is_quiet_about_recovery_when_handled_or_off(tmp_path, caplog):
    handled = make_cfg(do_recovery=True, last_split=5, max_splits=5, state_file=str(tmp_path / "state.json"))
    state.write(handled, "recovery_handled", True)
    with caplog.at_level(logging.INFO):
        psc._plan(handled, [], None)
        psc._plan(make_cfg(do_recovery=False, last_split=5, max_splits=5), [], None)
    assert _recovery_not_done_lines(caplog) == []
