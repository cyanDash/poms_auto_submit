import sys
import types

import poms_auto_submit as psc
from helpers import make_cfg


class RecordingSession:
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


def make_config_file(tmp_path, last_split=0, max_splits=5):
    config_path = tmp_path / "config.ini"
    config_path.write_text(f"[decision]\nlast_split = {last_split}\nmax_splits = {max_splits}\n")
    return config_path


def test_run_executes_plan_in_order_and_persists_last_split(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    recording = RecordingSession()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    monkeypatch.setattr(psc, "plan_next_slices", lambda cfg, session: [True, False])

    cfg = make_cfg(config_path=str(config_path), last_split=0)
    psc.run(cfg, dry_run=False)

    assert recording.calls == [
        ("set_subgroup", True),
        ("submit_next_slice",),
        ("set_subgroup", False),
        ("submit_next_slice",),
    ]
    assert cfg["last_split"] == 2
    assert "last_split = 2" in config_path.read_text()


def test_run_dry_run_does_not_submit_or_persist(monkeypatch, tmp_path):
    config_path = make_config_file(tmp_path)
    recording = RecordingSession()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    monkeypatch.setattr(psc, "plan_next_slices", lambda cfg, session: [True])

    cfg = make_cfg(config_path=str(config_path), last_split=0)
    psc.run(cfg, dry_run=True)

    assert recording.calls == []
    assert cfg["last_split"] == 0
    assert "last_split = 0" in config_path.read_text()


def test_run_stops_submitting_when_submit_next_slice_returns_none(monkeypatch, tmp_path):
    # submit_next_slice() returns None when POMS reports the campaign
    # stage's Input Dataset is exhausted -- treat as graceful completion,
    # not an error, and don't attempt any further planned slices this run.
    config_path = make_config_file(tmp_path)
    recording = RecordingSessionNoMoreSplits()
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())
    monkeypatch.setattr(psc, "PomsSession", lambda pc, cfg: recording)
    monkeypatch.setattr(psc, "plan_next_slices", lambda cfg, session: [True, False])

    cfg = make_cfg(config_path=str(config_path), last_split=0)
    psc.run(cfg, dry_run=False)

    assert recording.calls == [("set_subgroup", True), ("submit_next_slice",)]
    assert cfg["last_split"] == 0
    assert "last_split = 0" in config_path.read_text()
