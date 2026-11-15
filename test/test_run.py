import pytest
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

def run_with_cleanup_gate(monkeypatch, tmp_path, ready, dry_run):
    """Runs with an empty plan; returns (cfg, the run_cleanup calls)."""
    config_path = make_config_file(tmp_path)
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: SESSION)
    patch_snapshot(monkeypatch, plan=[], cleanup=ready)
    calls = []
    monkeypatch.setattr(
        psc.cleanup, "run_cleanup",
        lambda cfg, session, dry_run=False: calls.append((cfg, session, dry_run)),
    )

    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), last_split=0)
    psc.run(cfg, dry_run=dry_run)
    return cfg, calls


def test_run_calls_cleanup_when_ready(monkeypatch, tmp_path):
    cfg, calls = run_with_cleanup_gate(monkeypatch, tmp_path, ready=True, dry_run=False)

    assert calls == [(cfg, SESSION, False)]


def test_run_does_not_call_cleanup_when_not_ready(monkeypatch, tmp_path):
    for dry_run in (False, True):
        _, calls = run_with_cleanup_gate(monkeypatch, tmp_path, ready=False, dry_run=dry_run)

        assert calls == []


def test_run_dry_run_passes_dry_run_to_cleanup_when_ready(monkeypatch, tmp_path):
    cfg, calls = run_with_cleanup_gate(monkeypatch, tmp_path, ready=True, dry_run=True)

    assert calls == [(cfg, SESSION, True)]


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


# --- when output definitions are inferred (docs/adr/0023) ---

DEFINITIONS = [{"name": "x_reco1_sbnd", "depth": 1}]


def run_inference_case(monkeypatch, tmp_path, dry_run=False, plan=(), submit_exhausts=False, recovery_ready=False,
                       recorded=None, fail=False, **cfg_overrides):
    """Runs run() with a fake inference; returns (cfg, events) where events
    lists "infer(dry_run)" and "recovery" in call order."""
    events = []
    session = RecordingSessionNoMoreSplits() if submit_exhausts else SESSION
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: session)
    patch_snapshot(monkeypatch, plan=plan, recovery=recovery_ready)
    monkeypatch.setattr(psc.sam_client, "get_sam_client", lambda experiment, sam=None: "sam")

    def infer(cfg, session, sam, dry_run=False):
        events.append(f"infer({dry_run})")
        if fail:
            raise psc.output_definitions.OutputDefinitionsError("boom")
        if not dry_run:
            state.write(cfg, "output_definitions", DEFINITIONS)
        return DEFINITIONS

    monkeypatch.setattr(psc.output_definitions, "get_output_definitions", infer)
    monkeypatch.setattr(psc.recovery, "evaluate_and_run_recovery", lambda cfg, session: events.append("recovery"))
    monkeypatch.setattr(psc.stragglers, "manage", lambda *a, **k: None)
    monkeypatch.setattr(psc.cleanup, "run_cleanup", lambda *a, **k: events.append("cleanup"))
    cfg = make_cfg(cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), **cfg_overrides)
    if recorded is not None:
        state.write(cfg, "output_definitions", recorded)
    psc.run(cfg, dry_run=dry_run)
    return cfg, events


def test_run_infers_once_when_no_splits_are_left(monkeypatch, tmp_path):
    cfg, events = run_inference_case(monkeypatch, tmp_path, max_splits=5, last_split=5, do_cleanup=True)

    assert events == ["infer(False)"]
    assert state.read(cfg, "output_definitions") == DEFINITIONS


@pytest.mark.parametrize("flags", [{"do_recovery": True}, {"do_cleanup": True}])
def test_run_infers_for_either_flag(monkeypatch, tmp_path, flags):
    _, events = run_inference_case(monkeypatch, tmp_path, max_splits=5, last_split=5, **flags)

    assert events == ["infer(False)"]


def test_run_does_not_infer_without_a_flag_or_while_splits_remain(monkeypatch, tmp_path):
    assert run_inference_case(monkeypatch, tmp_path, max_splits=5, last_split=5)[1] == []
    assert run_inference_case(monkeypatch, tmp_path, max_splits=5, last_split=4, do_cleanup=True)[1] == []


def test_run_does_not_infer_again_once_recorded(monkeypatch, tmp_path):
    _, events = run_inference_case(
        monkeypatch, tmp_path, max_splits=5, last_split=5, do_cleanup=True, recorded=DEFINITIONS,
    )

    assert events == []


def test_run_infers_again_over_a_flat_list(monkeypatch, tmp_path):
    _, events = run_inference_case(
        monkeypatch, tmp_path, max_splits=5, last_split=5, do_cleanup=True, recorded=["x_reco1_sbnd"],
    )

    assert events == ["infer(False)"]


def test_dry_run_infers_and_records_nothing(monkeypatch, tmp_path):
    cfg, events = run_inference_case(monkeypatch, tmp_path, dry_run=True, max_splits=5, last_split=5, do_cleanup=True)

    assert events == ["infer(True)"]
    assert state.read(cfg, "output_definitions") is None


def test_inference_failure_is_logged_and_the_rest_of_the_run_goes_on(monkeypatch, tmp_path, caplog):
    cfg, events = run_inference_case(
        monkeypatch, tmp_path, fail=True, max_splits=5, last_split=5, do_cleanup=True,
    )

    assert events == ["infer(False)"]
    assert "will retry next run" in caplog.text
    assert state.read(cfg, "output_definitions") is None


def test_run_infers_before_recovery_when_poms_runs_out_of_splits(monkeypatch, tmp_path):
    _, events = run_inference_case(
        monkeypatch, tmp_path, plan=[True], submit_exhausts=True, recovery_ready=True,
        max_splits=5, last_split=2, do_recovery=True,
    )

    assert events == ["infer(False)", "recovery"]


def test_run_infers_once_when_poms_runs_out_and_recovery_is_not_ready(monkeypatch, tmp_path):
    _, events = run_inference_case(
        monkeypatch, tmp_path, plan=[True], submit_exhausts=True, recovery_ready=False,
        max_splits=5, last_split=2, do_recovery=True,
    )

    assert events == ["infer(False)"]
