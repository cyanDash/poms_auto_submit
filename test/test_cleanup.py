import json
import subprocess
import sys
import types

import pytest

import cleanup
from cleanup import _load_output_definitions, run_cleanup
from helpers import make_cfg


def fake_run(returncode=0, exc=None):
    def run(*args, **kwargs):
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout="", stderr="stderr text")
    return run


class FakeSession:
    def __init__(self, campaign_stage_id=42):
        self.campaign_stage_id = campaign_stage_id


@pytest.fixture(autouse=True)
def stub_poms_client(monkeypatch):
    # cleanup.py's lazy `from poms_auto_submit import persist_switch` needs
    # the module importable without real CVMFS/poms_client present.
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())


# --- _load_output_definitions ---

def test_load_output_definitions_missing_file_returns_empty(tmp_path):
    assert _load_output_definitions({"state_file": str(tmp_path / "state.json")}) == []


def test_load_output_definitions_dedups_preserving_first_seen_order(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"output_definitions": ['ds_a', 'ds_b', 'ds_a', 'ds_c', 'ds_b']}))

    assert _load_output_definitions({"state_file": str(tmp_path / "state.json")}) == ["ds_a", "ds_b", "ds_c"]


# --- run_cleanup ---

def persisted_switch(tmp_path):
    return json.loads((tmp_path / "state.json").read_text()).get("switch")


def test_run_cleanup_invokes_script_with_outdir_and_datasets_then_turns_switch_off(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text(json.dumps({"output_definitions": ['ds_a', 'ds_b']}))
    cfg = make_cfg(cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"))
    calls = []
    monkeypatch.setattr(
        cleanup.subprocess, "run",
        lambda *a, **kw: calls.append(a) or subprocess.CompletedProcess(a, 0, stdout="", stderr=""),
    )

    result = run_cleanup(cfg, FakeSession())

    (args,), = calls
    assert args == [cleanup.CLEANUP_SCRIPT, str(tmp_path), "ds_a", "ds_b"]
    assert result is True
    assert cfg["switch"] is False
    assert persisted_switch(tmp_path) is False


def test_run_cleanup_with_no_output_definitions_still_turns_switch_off(tmp_path):
    cfg = make_cfg(cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"))

    result = run_cleanup(cfg, FakeSession())

    assert result is True
    assert cfg["switch"] is False
    assert persisted_switch(tmp_path) is False


def test_run_cleanup_does_not_turn_switch_off_when_script_fails(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text(json.dumps({"output_definitions": ['ds_a']}))
    cfg = make_cfg(cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"), switch=True)
    monkeypatch.setattr(cleanup.subprocess, "run", fake_run(exc=subprocess.TimeoutExpired(cmd="x", timeout=1)))

    result = run_cleanup(cfg, FakeSession())

    assert result is False
    assert cfg["switch"] is True
    assert persisted_switch(tmp_path) is None
