import subprocess
import sys
import types

import pytest

import condor_progress as recovery_condor_progress
import recovery
from helpers import make_cfg
from recovery import evaluate_and_run_recovery, run_recovery_script


def fake_run(stdout="", returncode=0, exc=None):
    def run(*args, **kwargs):
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="stderr text")
    return run


# --- run_recovery_script ---

def test_run_recovery_script_returns_dataset_name(monkeypatch):
    monkeypatch.setattr(recovery.subprocess, "run", fake_run("0.5000\n0.98\nmy_recovery_dataset\n"))

    assert run_recovery_script("input_ds", "campaign", "/tmp/out.txt") == (0.5, 0.98, "my_recovery_dataset")


def test_run_recovery_script_returns_none_when_not_needed(monkeypatch):
    monkeypatch.setattr(recovery.subprocess, "run", fake_run("0.9900\n0.98\nNO_RECOVERY_NEEDED\n"))

    assert run_recovery_script("input_ds", "campaign", "/tmp/out.txt") == (0.99, 0.98, None)


def test_run_recovery_script_returns_none_ratio_when_input_dataset_empty(monkeypatch):
    monkeypatch.setattr(recovery.subprocess, "run", fake_run("N/A\n0.98\nNO_RECOVERY_NEEDED\n"))

    assert run_recovery_script("input_ds", "campaign", "/tmp/out.txt") == (None, 0.98, None)


def test_run_recovery_script_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(recovery.subprocess, "run", fake_run("", returncode=2))

    with pytest.raises(RuntimeError, match="stderr text"):
        run_recovery_script("input_ds", "campaign", "/tmp/out.txt")


class FakeSession:
    def __init__(self, progress=None, stage=None, submit_result="new-id"):
        self.campaign_stage_id = 42
        self._progress = progress if progress is not None else []
        self._stage = stage or {"dataset": "input_dataset"}
        self._submit_result = submit_result
        self.calls = []

    def get_progress(self):
        return self._progress

    def get_stage_params(self):
        return self._stage

    def set_recovery_input_dataset(self, dataset_name):
        self.calls.append(("set_recovery_input_dataset", dataset_name))

    def set_subgroup(self, use_pro):
        self.calls.append(("set_subgroup", use_pro))

    def submit_next_slice(self):
        self.calls.append(("submit_next_slice",))
        return self._submit_result


def make_config_file(tmp_path, last_split=3):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[decision]\nmax_splits = 5\ndo_recovery = 1\n")
    return config_path


def handled(tmp_path):
    import state
    return state.read({"state_file": str(tmp_path / "state.json")}, "recovery_handled", False)


def persisted_last_split(tmp_path):
    import state
    return state.read({"state_file": str(tmp_path / "state.json")}, "last_split")


def recorded_outputs(tmp_path):
    import state
    return state.read({"state_file": str(tmp_path / "state.json")}, "output_definitions")


@pytest.fixture(autouse=True)
def stub_poms_client(monkeypatch):
    # poms_auto_submit imports poms_client_bootstrap/poms_session at module
    # load; recovery.py's lazy `from poms_auto_submit import ...` needs the
    # module importable without real CVMFS/poms_client present.
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())


# --- _recovery_ready ---

def _cp(outcome, pct=None):
    return recovery_condor_progress.Progress(outcome, pct)


def _live_sub(submission_id, status, jobsub_job_id):
    return {"submission_id": submission_id, "status": status, "jobsub_job_id": jobsub_job_id}


def _by_job(outcomes):
    return lambda experiment, jobsub_job_id: outcomes[jobsub_job_id]


def _ready(tmp_path, progress, outcomes=None, **cfg_overrides):
    import poms_auto_submit as psc
    cfg = make_cfg(do_recovery=True, cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"))
    cfg.update(cfg_overrides)
    get_condor_progress = _by_job(outcomes) if outcomes is not None else None
    return psc._recovery_ready(cfg, FakeSession(progress=progress), get_condor_progress=get_condor_progress)


def test_recovery_ready_true_when_all_finished(tmp_path):
    progress = [_live_sub(1, "Failed", "1@s"), _live_sub(2, "Completed", "2@s")]
    assert _ready(tmp_path, progress, {"1@s": _cp("finished", 100.0), "2@s": _cp("no_data")}) is True


def test_recovery_ready_false_when_do_recovery_off(tmp_path):
    progress = [_live_sub(1, "Completed", "1@s")]
    assert _ready(tmp_path, progress, {"1@s": _cp("finished", 100.0)}, do_recovery=False) is False


def test_recovery_ready_false_when_already_handled(tmp_path):
    (tmp_path / "state.json").write_text('{"recovery_handled": true}')
    assert _ready(tmp_path, [_live_sub(1, "Completed", "1@s")], {"1@s": _cp("finished", 100.0)}) is False


def test_recovery_ready_false_when_no_submissions_yet(tmp_path):
    assert _ready(tmp_path, [], {}) is False


def test_recovery_ready_false_when_last_slice_still_active(tmp_path):
    assert _ready(tmp_path, [{"submission_id": 1, "status": "Running"}]) is False


def test_recovery_ready_false_for_false_failed_with_live_dag(tmp_path):
    assert _ready(tmp_path, [_live_sub(1, "Failed", "1@s")], {"1@s": _cp("live", 40.0)}) is False


def test_recovery_ready_false_for_false_located_with_live_dag(tmp_path):
    assert _ready(tmp_path, [_live_sub(1, "Located", "1@s")], {"1@s": _cp("live", 99.9)}) is False


def test_recovery_ready_false_when_earlier_slice_live_even_if_last_finished(tmp_path):
    progress = [_live_sub(1, "Running", "1@s"), _live_sub(2, "Completed", "2@s")]
    assert _ready(tmp_path, progress, {"1@s": _cp("live", 50.0), "2@s": _cp("finished", 100.0)}) is False


def test_recovery_ready_false_when_active_status_has_no_condor_data(tmp_path):
    assert _ready(tmp_path, [_live_sub(1, "Held", "1@s")], {"1@s": _cp("no_data")}) is False


def test_recovery_ready_false_on_condor_q_error(tmp_path):
    assert _ready(tmp_path, [_live_sub(1, "Completed", "1@s")], {"1@s": _cp("error")}) is False


def test_recovery_ready_never_touches_the_config(tmp_path):
    config_path = make_config_file(tmp_path)
    before = config_path.read_text()

    _ready(tmp_path, [{"submission_id": 1, "status": "Running"}], config_path=str(config_path))

    assert config_path.read_text() == before
    assert not handled(tmp_path)


# --- evaluate_and_run_recovery ---

def test_recovery_script_failure_does_not_persist_handled(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(
        config_path=str(config_path),
        cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), campaign_name="test_campaign",
    )
    session = FakeSession(progress=[{"submission_id": 1, "status": "Completed"}])
    monkeypatch.setattr(recovery, "run_recovery_script", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    assert evaluate_and_run_recovery(cfg, session) == "recovery_script_failed"
    assert session.calls == []
    assert not handled(tmp_path)


def test_no_recovery_needed(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(
        config_path=str(config_path),
        cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), campaign_name="test_campaign",
    )
    session = FakeSession(progress=[{"submission_id": 1, "status": "Located"}])
    monkeypatch.setattr(recovery, "run_recovery_script", lambda *a, **kw: (0.99, 0.98, None))

    assert evaluate_and_run_recovery(cfg, session) == "no_recovery_needed"
    assert session.calls == []
    assert handled(tmp_path)
    assert "do_recovery = 1" in config_path.read_text()


def test_recovery_submitted_resets_and_persists_last_split(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, last_split=5)
    cfg = make_cfg(
        config_path=str(config_path), last_split=5,
        cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), campaign_name="test_campaign",
    )
    session = FakeSession(
        progress=[{"submission_id": 1, "status": "Completed", "jobsub_job_id": None}],
        submit_result="new-sub-id",
    )
    calls = []

    def fake_script(input_dataset, campaign_name, output_path):
        calls.append((input_dataset, campaign_name, output_path))
        with open(output_path, "w") as f:
            f.write("ds_reco1\nds_caf\n")
        return 0.5, 0.98, "recovery_dataset_name"

    monkeypatch.setattr(recovery, "run_recovery_script", fake_script)

    result = evaluate_and_run_recovery(cfg, session)

    assert result == "recovery_submitted"
    assert session.calls == [
        ("set_recovery_input_dataset", "recovery_dataset_name"),
        ("set_subgroup", True),
        ("submit_next_slice",),
    ]
    assert cfg["last_split"] == 1
    assert persisted_last_split(tmp_path) == 1
    assert handled(tmp_path)
    assert "do_recovery = 1" in config_path.read_text()
    (input_dataset, campaign_name, output_path), = calls
    assert input_dataset == "input_dataset"
    assert campaign_name == "test_campaign"
    assert output_path.endswith("output_definitions_42.txt")
    assert recorded_outputs(tmp_path) == ["ds_reco1", "ds_caf"]
    assert not (tmp_path / "output_definitions_42.txt").exists()


def test_recovery_diverts_output_defnames_for_test_launch(tmp_path, monkeypatch):
    # cleanup.py's reader must never see a test-launch's output datasets.
    config_path = make_config_file(tmp_path, last_split=5)
    cfg = make_cfg(
        config_path=str(config_path), last_split=5,
        cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), campaign_name="test_campaign", test_launch=True,
    )
    session = FakeSession(
        progress=[{"submission_id": 1, "status": "Completed", "jobsub_job_id": None}],
    )
    calls = []

    def fake_script(input_dataset, campaign_name, output_path):
        calls.append((input_dataset, campaign_name, output_path))
        with open(output_path, "w") as f:
            f.write("ds_reco1\nds_caf\n")
        return 0.5, 0.98, "recovery_dataset_name"

    monkeypatch.setattr(recovery, "run_recovery_script", fake_script)

    evaluate_and_run_recovery(cfg, session)

    (_, _, output_path), = calls
    assert output_path.endswith("output_definitions_42_test_launch.txt")
    assert recorded_outputs(tmp_path) is None


def test_recovery_plan_failed_does_not_persist_handled(tmp_path, monkeypatch):
    # A transient POMS hiccup right after the dataset switch is retryable --
    # POMS's Input Dataset is already the recovery one, so the *ordinary*
    # next-hour run() picks it up regardless of the handled marker.
    config_path = make_config_file(tmp_path, last_split=5)
    cfg = make_cfg(
        config_path=str(config_path), last_split=5,
        cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), campaign_name="test_campaign",
    )
    session = FakeSession(
        progress=[{"submission_id": 1, "status": "Completed", "jobsub_job_id": None}],
    )

    def raise_get_progress_after_switch():
        raise RuntimeError("expired token")

    monkeypatch.setattr(recovery, "run_recovery_script", lambda *a, **kw: (0.5, 0.98, "recovery_dataset_name"))
    original_set_recovery = session.set_recovery_input_dataset

    def set_recovery_and_break_progress(dataset_name):
        original_set_recovery(dataset_name)
        session.get_progress = raise_get_progress_after_switch

    session.set_recovery_input_dataset = set_recovery_and_break_progress

    result = evaluate_and_run_recovery(cfg, session)

    assert result == "recovery_plan_failed"
    assert session.calls == [("set_recovery_input_dataset", "recovery_dataset_name")]
    assert not handled(tmp_path)
    assert cfg["last_split"] == 0
    assert persisted_last_split(tmp_path) == 0


def test_recovery_submit_failed_still_persists_handled(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, last_split=5)
    cfg = make_cfg(
        config_path=str(config_path), last_split=5,
        cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), campaign_name="test_campaign",
    )
    session = FakeSession(
        progress=[{"submission_id": 1, "status": "Completed", "jobsub_job_id": None}],
        submit_result=None,
    )
    monkeypatch.setattr(recovery, "run_recovery_script", lambda *a, **kw: (0.5, 0.98, "recovery_dataset_name"))

    result = evaluate_and_run_recovery(cfg, session)

    assert result == "recovery_submit_failed"
    assert handled(tmp_path)
    assert "do_recovery = 1" in config_path.read_text()
    # last_split was reset to 0 (new dataset) but never advanced to 1 since submit failed
    assert cfg["last_split"] == 0
    assert persisted_last_split(tmp_path) == 0


def test_any_still_running_true_for_live_dag_at_100_pct():
    import poms_auto_submit as psc
    from condor_progress import Progress
    from helpers import make_cfg
    subs = [{"submission_id": 1, "status": "Completed", "jobsub_job_id": "1@s"}]
    assert psc._any_still_running(make_cfg(), subs, lambda e, j: Progress("live", 100.0)) is True
