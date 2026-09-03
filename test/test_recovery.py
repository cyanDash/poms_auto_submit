import subprocess
import sys
import types

import pytest

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


# --- evaluate_and_run_recovery ---

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


def make_config_file(tmp_path, last_split=3, recovery_handled=0):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        f"[decision]\nlast_split = {last_split}\nmax_splits = 5\nrecovery_handled = {recovery_handled}\n"
    )
    return config_path


@pytest.fixture(autouse=True)
def stub_poms_client(monkeypatch):
    # poms_auto_submit imports poms_client_bootstrap/poms_session at module
    # load; recovery.py's lazy `from poms_auto_submit import ...` needs the
    # module importable without real CVMFS/poms_client present.
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())


def test_already_handled_short_circuits(tmp_path):
    cfg = make_cfg(config_path=str(make_config_file(tmp_path)), recovery_handled=True)
    session = FakeSession()

    assert evaluate_and_run_recovery(cfg, session) == "already_handled"
    assert session.calls == []


def test_waiting_when_last_slice_still_active(tmp_path):
    cfg = make_cfg(config_path=str(make_config_file(tmp_path)), recovery_handled=False)
    session = FakeSession(progress=[{"submission_id": 1, "status": "Running"}])

    assert evaluate_and_run_recovery(cfg, session) == "waiting"
    assert session.calls == []


def test_waiting_does_not_persist_recovery_handled(tmp_path):
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(config_path=str(config_path), recovery_handled=False)
    session = FakeSession(progress=[{"submission_id": 1, "status": "Running"}])

    evaluate_and_run_recovery(cfg, session)

    assert "recovery_handled = 0" in config_path.read_text()


def test_needs_manual_review_on_failed_status(tmp_path):
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(config_path=str(config_path), recovery_handled=False)
    session = FakeSession(progress=[{"submission_id": 1, "status": "Failed"}])

    assert evaluate_and_run_recovery(cfg, session) == "needs_manual_review"
    assert session.calls == []
    assert "recovery_handled = 1" in config_path.read_text()


def test_recovery_script_failure_does_not_persist_handled(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(
        config_path=str(config_path), recovery_handled=False,
        cache_dir=str(tmp_path), campaign_name="test_campaign",
    )
    session = FakeSession(progress=[{"submission_id": 1, "status": "Completed"}])
    monkeypatch.setattr(recovery, "run_recovery_script", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    assert evaluate_and_run_recovery(cfg, session) == "recovery_script_failed"
    assert session.calls == []
    assert "recovery_handled = 0" in config_path.read_text()


def test_no_recovery_needed(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(
        config_path=str(config_path), recovery_handled=False,
        cache_dir=str(tmp_path), campaign_name="test_campaign",
    )
    session = FakeSession(progress=[{"submission_id": 1, "status": "Located"}])
    monkeypatch.setattr(recovery, "run_recovery_script", lambda *a, **kw: (0.99, 0.98, None))

    assert evaluate_and_run_recovery(cfg, session) == "no_recovery_needed"
    assert session.calls == []
    assert "recovery_handled = 1" in config_path.read_text()


def test_recovery_submitted_resets_and_persists_last_split(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, last_split=5)
    cfg = make_cfg(
        config_path=str(config_path), recovery_handled=False, last_split=5,
        cache_dir=str(tmp_path), campaign_name="test_campaign",
    )
    session = FakeSession(
        progress=[{"submission_id": 1, "status": "Completed", "pct_complete": 100.0, "jobsub_job_id": None}],
        submit_result="new-sub-id",
    )
    calls = []
    monkeypatch.setattr(
        recovery, "run_recovery_script",
        lambda *a, **kw: calls.append(a) or (0.5, 0.98, "recovery_dataset_name"),
    )

    result = evaluate_and_run_recovery(cfg, session)

    assert result == "recovery_submitted"
    assert session.calls == [
        ("set_recovery_input_dataset", "recovery_dataset_name"),
        ("set_subgroup", True),
        ("submit_next_slice",),
    ]
    assert cfg["last_split"] == 1
    assert "last_split = 1" in config_path.read_text()
    assert "recovery_handled = 1" in config_path.read_text()
    (input_dataset, campaign_name, output_path), = calls
    assert input_dataset == "input_dataset"
    assert campaign_name == "test_campaign"
    assert output_path.endswith("output_definitions_42.txt")


def test_recovery_plan_failed_does_not_persist_handled(tmp_path, monkeypatch):
    # A transient POMS hiccup right after the dataset switch is retryable --
    # POMS's Input Dataset is already the recovery one, so the *ordinary*
    # next-hour run() picks it up regardless of recovery_handled.
    config_path = make_config_file(tmp_path, last_split=5)
    cfg = make_cfg(
        config_path=str(config_path), recovery_handled=False, last_split=5,
        cache_dir=str(tmp_path), campaign_name="test_campaign",
    )
    session = FakeSession(
        progress=[{"submission_id": 1, "status": "Completed", "pct_complete": 100.0, "jobsub_job_id": None}],
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
    assert "recovery_handled = 0" in config_path.read_text()
    assert cfg["last_split"] == 0
    assert "last_split = 0" in config_path.read_text()


def test_recovery_submit_failed_still_persists_handled(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, last_split=5)
    cfg = make_cfg(
        config_path=str(config_path), recovery_handled=False, last_split=5,
        cache_dir=str(tmp_path), campaign_name="test_campaign",
    )
    session = FakeSession(
        progress=[{"submission_id": 1, "status": "Completed", "pct_complete": 100.0, "jobsub_job_id": None}],
        submit_result=None,
    )
    monkeypatch.setattr(recovery, "run_recovery_script", lambda *a, **kw: (0.5, 0.98, "recovery_dataset_name"))

    result = evaluate_and_run_recovery(cfg, session)

    assert result == "recovery_submit_failed"
    assert "recovery_handled = 1" in config_path.read_text()
    # last_split was reset to 0 (new dataset) but never advanced to 1 since submit failed
    assert cfg["last_split"] == 0
    assert "last_split = 0" in config_path.read_text()
