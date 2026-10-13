import json
import os
import sys
import types

import pytest

import state
from cleanup import run_cleanup
from duplicate_cleanup import plan_path
from helpers import duplicated_campaign, make_cfg

DEFINITIONS = [{"name": "reco1_def", "depth": 1}, {"name": "larcv_def", "depth": 1}]
DUPLICATES = {"reco1_101.root", "larcv_101.root", "reco1_201.root", "larcv_201.root"}
INI_TEXT = "[poms]\ncampaign_name = test_campaign\n\n[decision]\ndo_cleanup = 1\n"


class NoCallSession:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected POMS call: {name}")


class NoLaunchCommandSession:
    """A stage whose output definitions cannot be inferred."""

    def get_launch_command(self):
        return None

    def get_stage_params(self):
        return {"dataset": "input_ds"}


@pytest.fixture(autouse=True)
def stub_poms_client(monkeypatch):
    # cleanup.py's lazy `from poms_auto_submit import persist_switch` needs
    # the module importable without real CVMFS/poms_client present.
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())


def make_cleanup_cfg(tmp_path, recorded=DEFINITIONS):
    ini = tmp_path / "my_stage.ini"
    ini.write_text(INI_TEXT)
    cfg = make_cfg(
        config_path=str(ini), cache_dir=str(tmp_path),
        state_file=str(tmp_path / "state_my_stage.json"), switch=True, do_cleanup=True,
    )
    if recorded is not None:
        state.write(cfg, "output_definitions", recorded)
    return cfg


def assert_switch_still_on(cfg):
    assert cfg["switch"] is True
    assert state.read(cfg, "switch") is None


# --- success ---

def test_successful_cleanup_turns_the_switch_off_in_the_state_file_only(tmp_path):
    cfg = make_cleanup_cfg(tmp_path)
    sam = duplicated_campaign()

    assert run_cleanup(cfg, NoCallSession(), sam=sam) is True

    assert sam.retired == DUPLICATES
    assert state.read(cfg, "switch") is False
    assert cfg["switch"] is False
    assert (tmp_path / "my_stage.ini").read_text() == INI_TEXT


def test_clean_campaign_retires_nothing_and_turns_the_switch_off(tmp_path):
    cfg = make_cleanup_cfg(tmp_path)
    sam = duplicated_campaign()
    sam.retired |= DUPLICATES

    assert run_cleanup(cfg, NoCallSession(), sam=sam) is True

    assert sam.retire_calls == []
    assert state.read(cfg, "switch") is False


def test_file_with_children_outside_the_plan_does_not_keep_the_switch_on(tmp_path, caplog):
    caplog.set_level("INFO")
    cfg = make_cleanup_cfg(tmp_path)
    sam = duplicated_campaign()
    sam.add_child_outside("reco1_101.root", "other_campaign.root")

    assert run_cleanup(cfg, NoCallSession(), sam=sam) is True

    assert "reco1_101.root" not in sam.retired
    assert state.read(cfg, "switch") is False
    assert "1 file(s) left in place, has children outside the plan" in caplog.text


def test_cleanup_removes_no_location_and_touches_nothing_on_disk(tmp_path, monkeypatch):
    cfg = make_cleanup_cfg(tmp_path)
    sam = duplicated_campaign()

    def refuse(*args, **kwargs):
        raise AssertionError("cleanup must not delete files or run commands")

    for target in ("os.remove", "os.unlink", "os.rmdir", "shutil.rmtree", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)

    assert run_cleanup(cfg, NoCallSession(), sam=sam) is True
    assert sam.location_removals == []


# --- definitions ---

def test_definitions_that_cannot_be_inferred_keep_the_switch_on_and_say_so(tmp_path, caplog):
    caplog.set_level("INFO")
    cfg = make_cleanup_cfg(tmp_path, recorded=None)
    sam = duplicated_campaign()

    assert run_cleanup(cfg, NoLaunchCommandSession(), sam=sam) is False

    assert_switch_still_on(cfg)
    assert sam.retire_calls == []
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("could not get the output definitions" in m and "switch stays on" in m for m in errors)
    assert "nothing to clean" not in caplog.text
    assert not os.path.exists(plan_path(cfg))


def test_old_flat_list_of_names_is_not_trusted(tmp_path):
    # A flat list came from unscoped discovery and can name other campaigns'
    # definitions; here it cannot be inferred again, so nothing is cleaned.
    cfg = make_cleanup_cfg(tmp_path, recorded=["reco1_def", "larcv_def"])
    sam = duplicated_campaign()

    assert run_cleanup(cfg, NoLaunchCommandSession(), sam=sam) is False

    assert_switch_still_on(cfg)
    assert sam.retire_calls == []


def test_missing_sam_client_keeps_the_switch_on(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "samweb_client", None)
    cfg = make_cleanup_cfg(tmp_path)

    assert run_cleanup(cfg, NoCallSession()) is False

    assert_switch_still_on(cfg)


# --- failures ---

def test_apply_failure_keeps_the_switch_on_and_is_retried_next_run(tmp_path, caplog):
    cfg = make_cleanup_cfg(tmp_path)
    sam = duplicated_campaign()
    sam.fail_on_retire = {"reco1_101.root"}

    assert run_cleanup(cfg, NoCallSession(), sam=sam) is False

    assert_switch_still_on(cfg)
    assert "switch stays on" in caplog.text

    sam.fail_on_retire = set()
    assert run_cleanup(cfg, NoCallSession(), sam=sam) is True
    assert sam.retired == DUPLICATES
    assert state.read(cfg, "switch") is False


def test_verification_failure_keeps_the_switch_on_and_names_the_input(tmp_path, caplog):
    cfg = make_cleanup_cfg(tmp_path)
    sam = duplicated_campaign()
    sam.retired.add("gen_2.root")

    assert run_cleanup(cfg, NoCallSession(), sam=sam) is False

    assert_switch_still_on(cfg)
    assert "verification failed for 1 input(s): gen_2.root" in caplog.text
    assert "switch stays on" in caplog.text


# --- dry run ---

def test_dry_run_writes_the_plan_and_retires_nothing(tmp_path, caplog):
    caplog.set_level("INFO")
    cfg = make_cleanup_cfg(tmp_path)
    sam = duplicated_campaign()

    assert run_cleanup(cfg, NoCallSession(), sam=sam, dry_run=True) is True

    with open(plan_path(cfg)) as f:
        plan = json.load(f)
    assert plan["inputs"]["gen_1.root"]["retired"] == {
        "larcv_def": ["larcv_101.root"], "reco1_def": ["reco1_101.root"],
    }
    assert sam.retire_calls == []
    assert sam.created_definitions == {}
    assert_switch_still_on(cfg)
    assert state.read(cfg, "cleanup") is None
    assert f"dry-run: plan written to {plan_path(cfg)}" in caplog.text
