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
    assert _load_output_definitions(str(tmp_path), 42) == []


def test_load_output_definitions_dedups_preserving_first_seen_order(tmp_path):
    (tmp_path / "output_definitions_42.txt").write_text("ds_a\nds_b\nds_a\nds_c\nds_b\n")

    assert _load_output_definitions(str(tmp_path), 42) == ["ds_a", "ds_b", "ds_c"]


def test_load_output_definitions_skips_blank_lines(tmp_path):
    (tmp_path / "output_definitions_42.txt").write_text("ds_a\n\n\nds_b\n")

    assert _load_output_definitions(str(tmp_path), 42) == ["ds_a", "ds_b"]


# --- run_cleanup ---

def make_config_file(tmp_path, switch=1):
    config_path = tmp_path / "config.ini"
    config_path.write_text(f"[decision]\nswitch = {switch}\n")
    return config_path


def test_run_cleanup_invokes_script_with_outdir_and_datasets_then_turns_switch_off(tmp_path, monkeypatch):
    (tmp_path / "output_definitions_42.txt").write_text("ds_a\nds_b\n")
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path))
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
    assert "switch = 0" in config_path.read_text()


def test_run_cleanup_with_no_output_definitions_still_turns_switch_off(tmp_path):
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path))

    result = run_cleanup(cfg, FakeSession())

    assert result is True
    assert cfg["switch"] is False
    assert "switch = 0" in config_path.read_text()


def test_run_cleanup_does_not_turn_switch_off_when_script_fails(tmp_path, monkeypatch):
    (tmp_path / "output_definitions_42.txt").write_text("ds_a\n")
    config_path = make_config_file(tmp_path)
    cfg = make_cfg(config_path=str(config_path), cache_dir=str(tmp_path), switch=True)
    monkeypatch.setattr(cleanup.subprocess, "run", fake_run(exc=subprocess.TimeoutExpired(cmd="x", timeout=1)))

    result = run_cleanup(cfg, FakeSession())

    assert result is False
    assert cfg["switch"] is True
    assert "switch = 1" in config_path.read_text()
