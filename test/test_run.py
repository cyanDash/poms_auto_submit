import sys
import types

import poms_auto_submit as psc
import state
from helpers import make_cfg


class RecordingSession:
    campaign_stage_id = 42

    def __init__(self):
        self.calls = []

    def set_subgroup(self, use_pro):
        self.calls.append(("set_subgroup", use_pro))

    def submit_next_slice(self):
        self.calls.append(("submit_next_slice",))
        return "123"


class RecordingSessionNoMoreSplits(RecordingSession):
    def submit_next_slice(self):
        self.calls.append(("submit_next_slice",))
        return None


class FakeSnapshot:
    def __init__(self, plan, cleanup, recovery):
        self._plan, self._cleanup, self._recovery = plan, cleanup, recovery

    def slices_to_plan(self):
        return list(self._plan)

    def record_stuck(self):
        pass

    def cleanup_ready(self):
        return self._cleanup() if callable(self._cleanup) else self._cleanup

    def recovery_ready(self):
        return self._recovery


def patch_snapshot(monkeypatch, plan=(), cleanup=False, recovery=False):
    monkeypatch.setattr(
        psc.CampaignSnapshot, "take",
        classmethod(lambda cls, cfg, session, get_condor_progress=None: FakeSnapshot(plan, cleanup, recovery)),
    )


def make_config_file(tmp_path, last_split=0, max_splits=5):
    config_path = tmp_path / "config.ini"
    config_path.write_text(f"[decision]\nmax_splits = {max_splits}\n")
    return config_path


def test_run_executes_plan_in_order_and_persists_last_split(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    recording = RecordingSession()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    patch_snapshot(monkeypatch, plan=[True, False])

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=False)

    assert recording.calls == [
        ("set_subgroup", True),
        ("submit_next_slice",),
        ("set_subgroup", False),
        ("submit_next_slice",),
    ]
    assert cfg["last_split"] == 2
    assert state.read(cfg, "last_split") == 2


def test_run_dry_run_does_not_submit_or_persist(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    recording = RecordingSession()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    patch_snapshot(monkeypatch, plan=[True])

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=True)

    assert recording.calls == []
    assert cfg["last_split"] == 0
    assert state.read(cfg, "last_split") is None


def test_run_stops_submitting_when_submit_next_slice_returns_none(monkeypatch, tmp_path):
    # submit_next_slice() returns None when POMS reports the campaign
    # stage's Input Dataset is exhausted -- treat as graceful completion,
    # not an error, and don't attempt any further planned slices this run.
    config_path = make_config_file(tmp_path)
    recording = RecordingSessionNoMoreSplits()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    patch_snapshot(monkeypatch, plan=[True, False], recovery=False)

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=False)

    assert recording.calls == [("set_subgroup", True), ("submit_next_slice",)]
    assert cfg["last_split"] == 0
    assert state.read(cfg, "last_split") is None


def test_run_calls_recovery_when_submit_next_slice_returns_none(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    recording = RecordingSessionNoMoreSplits()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    patch_snapshot(monkeypatch, plan=[True], recovery=True)
    calls = []
    monkeypatch.setattr(psc.recovery, "evaluate_and_run_recovery", lambda cfg, session: calls.append((cfg, session)))

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=False)

    assert len(calls) == 1
    assert calls[0] == (cfg, recording)


def test_run_does_not_call_recovery_when_not_ready(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    recording = RecordingSessionNoMoreSplits()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    patch_snapshot(monkeypatch, plan=[True], recovery=False)
    calls = []
    monkeypatch.setattr(psc.recovery, "evaluate_and_run_recovery", lambda cfg, session: calls.append(1))

    psc.run(make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0), dry_run=False)

    assert calls == []


def test_run_does_not_call_recovery_when_a_slice_is_submitted(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    recording = RecordingSession()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    patch_snapshot(monkeypatch, plan=[True])
    calls = []
    monkeypatch.setattr(psc.recovery, "evaluate_and_run_recovery", lambda cfg, session: calls.append(1))

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=False)

    assert calls == []


# --- cleanup trigger, when plan comes back empty ---

SESSION = types.SimpleNamespace(campaign_stage_id=42)

def test_run_calls_cleanup_when_ready(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: SESSION)
    patch_snapshot(monkeypatch, plan=[], cleanup=True)
    calls = []
    monkeypatch.setattr(psc.cleanup, "run_cleanup", lambda cfg, session: calls.append((cfg, session)))

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=False)

    assert calls == [(cfg, SESSION)]


def test_run_does_not_call_cleanup_when_not_ready(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: SESSION)
    patch_snapshot(monkeypatch, plan=[], cleanup=False)
    calls = []
    monkeypatch.setattr(psc.cleanup, "run_cleanup", lambda cfg, session: calls.append(1))

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=False)

    assert calls == []


def test_run_dry_run_does_not_call_cleanup_even_when_ready(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: SESSION)
    patch_snapshot(monkeypatch, plan=[], cleanup=True)
    calls = []
    monkeypatch.setattr(psc.cleanup, "run_cleanup", lambda cfg, session: calls.append(1))

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=True)

    assert calls == []


def test_run_manages_stragglers_only_when_no_splits_left(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: types.SimpleNamespace(
        get_progress=lambda: [], campaign_stage_id=42))
    patch_snapshot(monkeypatch, plan=[], cleanup=False)
    monkeypatch.setattr(psc.stragglers, "manage", lambda *a, **k: calls.append(k.get("dry_run")))

    psc.run(make_cfg(max_splits=5, last_split=4), dry_run=False)
    assert calls == []
    psc.run(make_cfg(max_splits=5, last_split=5), dry_run=True)
    assert calls == [True]


def test_run_survives_straggler_cache_write_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: types.SimpleNamespace(
        get_progress=lambda: [], campaign_stage_id=42))
    patch_snapshot(monkeypatch, plan=[], cleanup=lambda: ready.append(1) or False)
    ready = []

    def boom(*a, **k):
        raise FileNotFoundError("no cache_dir")
    monkeypatch.setattr(psc.stragglers, "manage", boom)

    psc.run(make_cfg(max_splits=5, last_split=5), dry_run=False)
    assert ready == [1]


def test_run_test_launch_only_submits(monkeypatch):
    class OnlySubmits:
        def __init__(self):
            self.calls = []

        def submit_next_slice(self):
            self.calls.append("submit_next_slice")
            return "1"

    session = OnlySubmits()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: session)

    psc.run_test_launch(make_cfg(test_launch=True, last_split=5, max_splits=5), dry_run=False)

    assert session.calls == ["submit_next_slice"]


def test_run_test_launch_dry_run_submits_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: (_ for _ in ()).throw(AssertionError("session")))

    psc.run_test_launch(make_cfg(test_launch=True), dry_run=True)
