import json
import os
import subprocess
import sys

import pytest

import duplicate_cleanup
import state
from duplicate_cleanup import clean_definitions, plan_cleanup, verify_plan
from helpers import FakeSam, duplicated_campaign

RECO1 = {"name": "reco1_def", "depth": 1}
LARCV = {"name": "larcv_def", "depth": 1}
CAF = {"name": "caf_def", "depth": 2}


def plan_path(cfg):
    return os.path.join(cfg["cache_dir"], "cleanup_plan_my_stage.json")


def retired(plan):
    return {d["name"]: d["retire"] for d in plan["definitions"]}


# --- kept worker ---

def test_one_worker_per_input_plans_no_retirement():
    sam = FakeSam()
    for i, pid in ((1, 100), (2, 200)):
        sam.add("reco1_def", f"reco1_{i}.root", f"gen_{i}.root", pid)
        sam.add("larcv_def", f"larcv_{i}.root", f"gen_{i}.root", pid)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert retired(plan) == {"larcv_def": [], "reco1_def": []}
    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "larcv_def": ["larcv_1.root"], "reco1_def": ["reco1_1.root"],
    }
    assert plan["inputs"]["gen_1.root"]["fallback"] is False
    assert sam.lineage_calls == []


def test_complete_worker_is_kept_in_every_tier_over_a_partial_one():
    sam = FakeSam()
    # The partial worker has the lower process id and the first file name.
    sam.add("larcv_def", "larcv_a.root", "gen_1.root", 100)
    sam.add("larcv_def", "larcv_b.root", "gen_1.root", 200)
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 200
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "larcv_def": ["larcv_b.root"], "reco1_def": ["reco1_b.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {"larcv_def": ["larcv_a.root"]}
    assert retired(plan) == {
        "larcv_def": [{"file": "larcv_a.root", "input": "gen_1.root", "process_id": 100}],
        "reco1_def": [],
    }


def test_only_worker_being_partial_is_kept():
    sam = FakeSam()
    sam.add("larcv_def", "larcv_a.root", "gen_1.root", 100)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert plan["inputs"]["gen_1.root"]["kept"] == {"larcv_def": ["larcv_a.root"]}
    assert retired(plan) == {"larcv_def": [], "reco1_def": []}


def test_tie_goes_to_the_lowest_process_id_on_every_run():
    def build(pids):
        sam = FakeSam()
        for pid in pids:
            sam.add("reco1_def", f"reco1_{pid}.root", "gen_1.root", pid)
            sam.add("larcv_def", f"larcv_{pid}.root", "gen_1.root", pid)
        return sam

    first = plan_cleanup([RECO1, LARCV], build([300, 200]))
    second = plan_cleanup([LARCV, RECO1], build([200, 300]))

    assert first["inputs"]["gen_1.root"]["kept_process_id"] == 200
    assert [r["file"] for r in retired(first)["reco1_def"]] == ["reco1_300.root"]
    assert [r["file"] for r in retired(first)["larcv_def"]] == ["larcv_300.root"]
    assert json.dumps(first) == json.dumps(second)


def test_every_other_worker_is_retired_even_in_a_tier_the_kept_one_lacks():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("larcv_def", "larcv_a.root", "gen_1.root", 100)
    sam.add("caf_flat_def", "caf_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1, LARCV, {"name": "caf_flat_def", "depth": 1}], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert [r["file"] for r in retired(plan)["caf_flat_def"]] == ["caf_b.root"]


# --- fallback ---

def test_no_process_id_keeps_first_file_by_name_per_definition():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_b.root", "gen_1.root")
    sam.add("reco1_def", "reco1_a.root", "gen_1.root")
    sam.add("larcv_def", "larcv_z.root", "gen_1.root")
    sam.add("reco1_def", "reco1_c.root", "gen_2.root", 100)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert plan["fallback_used"] is True
    assert plan["inputs"]["gen_1.root"]["fallback"] is True
    assert plan["inputs"]["gen_1.root"]["kept_process_id"] is None
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "larcv_def": ["larcv_z.root"], "reco1_def": ["reco1_a.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {"reco1_def": ["reco1_b.root"]}
    assert plan["inputs"]["gen_2.root"]["fallback"] is False


def test_one_file_without_process_id_puts_the_whole_input_on_fallback():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root")
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1], sam)

    assert plan["inputs"]["gen_1.root"]["fallback"] is True
    assert plan["inputs"]["gen_1.root"]["kept"] == {"reco1_def": ["reco1_a.root"]}


def test_fallback_is_not_flagged_when_every_file_has_a_process_id():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)

    assert plan_cleanup([RECO1], sam)["fallback_used"] is False


# --- children guard ---

def test_file_with_a_live_child_outside_the_plan_is_skipped():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)
    sam.add_child_outside("reco1_b.root", "other_campaign.root")

    plan = plan_cleanup([RECO1], sam)

    reason = "has children that are not being retired: other_campaign.root"
    assert retired(plan) == {"reco1_def": []}
    assert plan["definitions"][0]["skipped"] == [
        {"file": "reco1_b.root", "input": "gen_1.root", "process_id": 200, "reason": reason},
    ]
    assert plan["inputs"]["gen_1.root"]["skipped"] == {
        "reco1_def": [{"file": "reco1_b.root", "reason": reason}],
    }


def test_empty_child_list_means_no_children():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1], sam)

    assert sam.getFileLineage("children", "reco1_b.root") == []
    assert [r["file"] for r in retired(plan)["reco1_def"]] == ["reco1_b.root"]


def test_children_are_looked_up_only_for_files_to_retire():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)
    sam.add("reco1_def", "reco1_c.root", "gen_2.root", 300)

    plan_cleanup([RECO1], sam)

    assert sam.lineage_calls == ["reco1_b.root"]


# --- depth ---

def two_level_campaign():
    """caf files are children of the reco1 file of the same worker."""
    sam = FakeSam()
    for pid in (100, 200):
        sam.add("reco1_def", f"reco1_{pid}.root", "gen_1.root", pid)
        sam.add("caf_def", f"caf_{pid}.root", f"reco1_{pid}.root", pid)
    return sam


def test_definitions_are_listed_deepest_first():
    deeper = {"name": "a_hist_def", "depth": 3}

    plan = plan_cleanup([RECO1, deeper, CAF, LARCV], FakeSam())

    assert [(d["name"], d["depth"]) for d in plan["definitions"]] == [
        ("a_hist_def", 3), ("caf_def", 2), ("larcv_def", 1), ("reco1_def", 1),
    ]


def test_depth_two_file_groups_under_its_root_ancestor():
    plan = plan_cleanup([RECO1, CAF], two_level_campaign())

    assert list(plan["inputs"]) == ["gen_1.root"]
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "caf_def": ["caf_100.root"], "reco1_def": ["reco1_100.root"],
    }


def test_parent_is_retired_when_its_child_is_retired_too():
    plan = plan_cleanup([RECO1, CAF], two_level_campaign())

    assert [r["file"] for r in retired(plan)["caf_def"]] == ["caf_200.root"]
    assert [r["file"] for r in retired(plan)["reco1_def"]] == ["reco1_200.root"]


def test_parent_of_a_skipped_child_is_skipped():
    sam = two_level_campaign()
    sam.add_child_outside("caf_200.root", "other_campaign.root")

    plan = plan_cleanup([RECO1, CAF], sam)

    assert retired(plan) == {"caf_def": [], "reco1_def": []}
    assert [s["file"] for s in plan["definitions"][1]["skipped"]] == ["reco1_200.root"]


def test_depth_two_ancestor_outside_the_definitions_is_fetched():
    sam = FakeSam()
    for pid in (100, 200):
        sam.add(None, f"reco1_{pid}.root", "gen_1.root", pid)
        sam.add("caf_def", f"caf_{pid}.root", f"reco1_{pid}.root", pid)

    plan = plan_cleanup([CAF], sam)

    assert plan["inputs"]["gen_1.root"]["retired"] == {"caf_def": ["caf_200.root"]}


# --- chained stages: deeper tiers written by other jobs ---

def chained_campaign():
    """Two reco1 jobs for one input, each followed by its own caf job."""
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)
    sam.add("caf_def", "caf_a.root", "reco1_a.root", 300)
    sam.add("caf_def", "caf_b.root", "reco1_b.root", 400)
    return sam


def test_one_chain_is_kept_and_the_other_retired():
    plan = plan_cleanup([RECO1, CAF], chained_campaign())

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "caf_def": ["caf_a.root"], "reco1_def": ["reco1_a.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {
        "caf_def": ["caf_b.root"], "reco1_def": ["reco1_b.root"],
    }
    assert plan["fallback_used"] is False


def test_chain_with_more_tiers_is_kept_over_a_lower_process_id():
    sam = chained_campaign()
    sam.retired.add("caf_a.root")

    plan = plan_cleanup([RECO1, CAF], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 200
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "caf_def": ["caf_b.root"], "reco1_def": ["reco1_b.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {"reco1_def": ["reco1_a.root"]}


def test_duplicates_under_a_kept_parent_keep_one_worker():
    sam = chained_campaign()
    sam.add("caf_def", "caf_a_again.root", "reco1_a.root", 250)

    plan = plan_cleanup([RECO1, CAF], sam)

    assert plan["inputs"]["gen_1.root"]["kept"]["caf_def"] == ["caf_a_again.root"]
    assert plan["inputs"]["gen_1.root"]["retired"] == {
        "caf_def": ["caf_a.root", "caf_b.root"], "reco1_def": ["reco1_b.root"],
    }


def test_three_levels_keep_one_chain():
    sam = chained_campaign()
    sam.add("hist_def", "hist_a.root", "caf_a.root", 500)
    sam.add("hist_def", "hist_b.root", "caf_b.root", 50)

    plan = plan_cleanup([RECO1, CAF, {"name": "hist_def", "depth": 3}], sam)

    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "caf_def": ["caf_a.root"], "hist_def": ["hist_a.root"], "reco1_def": ["reco1_a.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {
        "hist_def": ["hist_b.root"], "caf_def": ["caf_b.root"], "reco1_def": ["reco1_b.root"],
    }


def test_chained_campaign_is_cleaned_and_verified(tmp_path, caplog):
    caplog.set_level("INFO")
    sam = chained_campaign()

    result = clean_definitions(make_cfg(tmp_path), [RECO1, CAF], sam)

    assert result["ok"] is True
    assert sam.retire_calls == ["caf_b.root", "reco1_b.root"]
    assert (
        "cleanup: retired caf_b.root from caf_def (input gen_1.root, parent reco1_b.root is retired)"
    ) in caplog.text
    assert (
        "cleanup: retired reco1_b.root from reco1_def (input gen_1.root, kept process id 100)"
    ) in caplog.text


# --- children guard: retired children ---

def test_retired_child_does_not_block_its_parent():
    sam = two_level_campaign()
    sam.retired.add("caf_200.root")

    plan = plan_cleanup([RECO1, CAF], sam)

    assert [r["file"] for r in retired(plan)["reco1_def"]] == ["reco1_200.root"]
    assert plan["definitions"][1]["skipped"] == []


# --- grouping ---

def test_data_file_hashed_stream_suffix_is_stripped():
    sam = FakeSam()
    raw = "data_EventBuilder3_p2_run18250_10_strmBNBZeroBias"
    sam.add("reco1_def", "reco1_a.root", f"{raw}-0a1b2c3d.root", 100)
    sam.add("reco1_def", "reco1_b.root", f"{raw}-9f8e7d6c.root", 200)
    sam.add("reco1_def", "reco1_c.root", "data_EventBuilder3_p2_run18250_11_strmBNBZeroBias-11.root", 300)

    plan = plan_cleanup([RECO1], sam)

    assert sorted(plan["inputs"]) == [
        "data_EventBuilder3_p2_run18250_10_strmBNBZeroBias",
        "data_EventBuilder3_p2_run18250_11_strmBNBZeroBias",
    ]
    assert [r["file"] for r in retired(plan)["reco1_def"]] == ["reco1_b.root"]


def test_mc_input_names_with_dashes_are_kept_whole():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "reco1-detsim-g4-gen-Gen2_2026-d00c.root", 100)
    sam.add("reco1_def", "reco1_b.root", "reco1-detsim-g4-gen-Gen2_2026-d00d.root", 200)

    plan = plan_cleanup([RECO1], sam)

    assert sorted(plan["inputs"]) == [
        "reco1-detsim-g4-gen-Gen2_2026-d00c.root", "reco1-detsim-g4-gen-Gen2_2026-d00d.root",
    ]
    assert retired(plan) == {"reco1_def": []}


def test_file_that_cannot_be_grouped_is_reported_and_not_retired():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("reco1_def", "reco1_orphan.root", None, 200)
    sam.definitions["reco1_def"].append("reco1_unknown.root")

    plan = plan_cleanup([RECO1], sam)

    assert plan["definitions"][0]["ungrouped"] == [
        {"file": "reco1_orphan.root", "reason": "reco1_orphan.root has no parent"},
        {"file": "reco1_unknown.root", "reason": "no metadata in SAM for reco1_unknown.root"},
    ]
    assert retired(plan) == {"reco1_def": []}


def test_file_whose_recorded_depth_stops_at_an_output_file_is_ungrouped():
    too_shallow = {"name": "caf_def", "depth": 1}

    plan = plan_cleanup([RECO1, too_shallow], chained_campaign())

    by_name = {d["name"]: d for d in plan["definitions"]}
    assert [u["file"] for u in by_name["caf_def"]["ungrouped"]] == ["caf_a.root", "caf_b.root"]
    assert "depth 1 does not lead to an input" in by_name["caf_def"]["ungrouped"][0]["reason"]


def ungrouped_campaign():
    sam = duplicated_campaign()
    sam.add("reco1_def", "reco1_orphan.root", None, 103)
    sam.definitions["reco1_def"].append("reco1_unknown.root")
    return sam


@pytest.mark.parametrize("dry_run", [False, True])
def test_ungrouped_files_fail_the_cleanup_before_anything_is_retired(tmp_path, dry_run):
    sam = ungrouped_campaign()
    cfg = make_cfg(tmp_path)

    result = clean_definitions(cfg, [RECO1, LARCV], sam, dry_run=dry_run)

    assert result["ok"] is False
    assert "reco1_orphan.root" in result["failure"]["error"]
    assert "reco1_unknown.root" in result["failure"]["error"]
    assert sam.retire_calls == []
    assert sam.created_definitions == {}
    assert state.read(cfg, "cleanup") is None
    with open(result["plan_path"]) as f:
        assert len(json.load(f)["definitions"][1]["ungrouped"]) == 2


def test_wrong_recorded_depth_fails_the_cleanup(tmp_path):
    sam = chained_campaign()

    result = clean_definitions(make_cfg(tmp_path), [RECO1, {"name": "caf_def", "depth": 1}], sam)

    assert result["ok"] is False
    assert "caf_a.root" in result["failure"]["error"]
    assert sam.retire_calls == []


def test_cleanup_is_planned_afresh_once_the_ungrouped_file_is_gone(tmp_path):
    sam = ungrouped_campaign()
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is False
    sam.definitions["reco1_def"].remove("reco1_orphan.root")
    sam.definitions["reco1_def"].remove("reco1_unknown.root")

    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True

    assert sam.retired == {"reco1_101.root", "reco1_201.root"}


# --- bulk reads and totals ---

def test_metadata_is_read_in_batches(monkeypatch):
    monkeypatch.setattr(duplicate_cleanup, "METADATA_BATCH_SIZE", 2)
    sam = FakeSam()
    for i in range(5):
        sam.add("reco1_def", f"reco1_{i}.root", f"gen_{i}.root", i)

    plan = plan_cleanup([RECO1], sam)

    assert sam.batch_sizes == [2, 2, 1]
    assert len(plan["inputs"]) == 5


def test_totals_per_definition():
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)
    sam.add("reco1_def", "reco1_c.root", "gen_1.root", 300)
    sam.add("reco1_def", "reco1_d.root", "gen_2.root", 400)
    sam.add("reco1_def", "reco1_orphan.root", None, 500)
    sam.add_child_outside("reco1_c.root", "other_campaign.root")

    plan = plan_cleanup([RECO1], sam)

    assert plan["definitions"][0]["totals"] == {
        "files": 5, "kept": 2, "retired": 1, "skipped": 1, "ungrouped": 1,
    }


# --- plan file ---

def test_plan_file_is_named_after_the_ini_and_reads_back(tmp_path):
    cfg = {
        "cache_dir": str(tmp_path / "campaign"),
        "state_file": str(tmp_path / "campaign" / "state_my_stage.json"),
    }
    sam = FakeSam()
    sam.add("reco1_def", "reco1_a.root", "gen_1.root", 100)
    sam.add("reco1_def", "reco1_b.root", "gen_1.root", 200)

    result = clean_definitions(cfg, [RECO1], sam, dry_run=True)

    assert result["plan_path"] == str(tmp_path / "campaign" / "cleanup_plan_my_stage.json")
    with open(result["plan_path"]) as f:
        assert json.load(f) == plan_cleanup([RECO1], sam)
    assert [p.name for p in (tmp_path / "campaign").iterdir()] == ["cleanup_plan_my_stage.json"]


def test_module_imports_without_samweb_client():
    code = (
        "import sys; sys.modules['samweb_client'] = None; "
        f"sys.path.insert(0, {os.path.dirname(duplicate_cleanup.__file__)!r}); "
        "import duplicate_cleanup"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


# --- apply ---

def make_cfg(tmp_path):
    return {
        "cache_dir": str(tmp_path),
        "state_file": str(tmp_path / "state_my_stage.json"),
    }


def test_apply_retires_the_other_workers_files_and_removes_no_location(tmp_path):
    sam = duplicated_campaign()

    result = clean_definitions(make_cfg(tmp_path), [RECO1, LARCV], sam)

    assert result["ok"] is True
    assert sam.retired == {"reco1_101.root", "larcv_101.root", "reco1_201.root", "larcv_201.root"}
    assert sam.location_removals == []


def test_nothing_on_disk_is_touched(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("cleanup must not delete files or run commands")

    for target in ("os.remove", "os.unlink", "os.rmdir", "shutil.rmtree", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)

    assert clean_definitions(make_cfg(tmp_path), [RECO1, LARCV], duplicated_campaign())["ok"] is True


def test_each_retirement_is_logged_with_its_input_and_kept_process_id(tmp_path, caplog):
    caplog.set_level("INFO")

    clean_definitions(make_cfg(tmp_path), [RECO1], duplicated_campaign())

    lines = [r.getMessage() for r in caplog.records if "reco1_201.root" in r.getMessage()]
    assert lines == [
        "cleanup: retired reco1_201.root from reco1_def (input gen_2.root, kept process id 200)",
    ]


def test_definitions_are_applied_in_series_deepest_first(tmp_path):
    sam = two_level_campaign()

    clean_definitions(make_cfg(tmp_path), [RECO1, CAF], sam)

    assert sam.retire_calls == ["caf_200.root", "reco1_200.root"]


def test_failure_in_one_definition_stops_the_rest_and_is_reported(tmp_path, caplog):
    sam = two_level_campaign()
    sam.fail_on_retire = {"caf_200.root"}

    result = clean_definitions(make_cfg(tmp_path), [RECO1, CAF], sam)

    assert result["ok"] is False
    assert result["failure"] == {
        "definition": "caf_def", "file": "caf_200.root",
        "error": "500 Server Error retiring caf_200.root",
    }
    assert sam.retire_calls == ["caf_200.root"]
    assert sam.retired == set()
    assert "cleanup: retiring caf_200.root from caf_def failed" in caplog.text


def test_file_already_retired_counts_as_success(tmp_path, caplog):
    caplog.set_level("INFO")
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)
    sam.fail_on_retire = {"reco1_201.root"}
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is False
    assert sam.retired == {"reco1_101.root"}
    sam.fail_on_retire = {"reco1_101.root"}
    sam.retire_calls.clear()

    result = clean_definitions(cfg, [RECO1], sam)

    assert result["ok"] is True
    assert sam.retire_calls == ["reco1_201.root"]
    assert "cleanup: reco1_101.root from reco1_def is already retired" in caplog.text


# --- retired-files definition ---

def test_retired_files_definition_is_created_per_cleaned_definition(tmp_path):
    sam = duplicated_campaign()
    sam.add("caf_flat_def", "caf_100.root", "gen_1.root", 100)

    clean_definitions(make_cfg(tmp_path), [RECO1, LARCV, {"name": "caf_flat_def", "depth": 1}], sam)

    assert sam.created_definitions == {
        "reco1_def_retired_files": "dataset.tag reco1_def and availability: retired",
        "larcv_def_retired_files": "dataset.tag larcv_def and availability: retired",
    }


def test_existing_retired_files_definition_is_left_alone(tmp_path):
    sam = duplicated_campaign()
    sam.created_definitions["reco1_def_retired_files"] = "made by an earlier cleanup"

    result = clean_definitions(make_cfg(tmp_path), [RECO1], sam)

    assert result["ok"] is True
    assert sam.created_definitions == {"reco1_def_retired_files": "made by an earlier cleanup"}


def test_failure_looking_up_the_retired_files_definition_is_reported(tmp_path):
    sam = duplicated_campaign()

    def broken(defname):
        raise RuntimeError("503 Service Unavailable")

    sam.descDefinitionDict = broken

    result = clean_definitions(make_cfg(tmp_path), [RECO1, LARCV], sam)

    assert result["ok"] is False
    assert result["failure"] == {
        "definition": "larcv_def", "file": None, "error": "503 Service Unavailable",
    }
    assert sam.created_definitions == {}
    assert not any(f.startswith("reco1") for f in sam.retire_calls)


# --- dry run ---

def test_dry_run_writes_the_plan_and_changes_nothing(tmp_path, caplog):
    caplog.set_level("INFO")
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)

    result = clean_definitions(cfg, [RECO1], sam, dry_run=True)

    assert result == {
        "ok": True, "plan_path": plan_path(cfg), "failure": None, "bad_inputs": {}, "skipped": [],
    }
    with open(plan_path(cfg)) as f:
        assert json.load(f) == plan_cleanup([RECO1], duplicated_campaign())
    assert sam.retire_calls == []
    assert sam.created_definitions == {}
    assert state.read(cfg, "cleanup") is None
    assert (
        "cleanup: dry run: would retire reco1_201.root from reco1_def "
        "(input gen_2.root, kept process id 200)"
    ) in caplog.text


def test_real_run_writes_the_same_plan_as_a_dry_run(tmp_path):
    dry, real = make_cfg(tmp_path / "dry"), make_cfg(tmp_path / "real")

    clean_definitions(dry, [RECO1, LARCV], duplicated_campaign(), dry_run=True)
    clean_definitions(real, [RECO1, LARCV], duplicated_campaign())

    with open(plan_path(dry)) as f_dry, open(plan_path(real)) as f_real:
        assert json.load(f_dry) == json.load(f_real)


# --- resume ---

def failed_first_run(tmp_path):
    """larcv_def is cleaned, then reco1_def fails on its first file."""
    sam = duplicated_campaign()
    sam.fail_on_retire = {"reco1_101.root"}
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1, LARCV], sam)["ok"] is False
    sam.fail_on_retire = set()
    sam.retire_calls.clear()
    return cfg, sam


def test_rerun_after_a_failure_skips_completed_definitions(tmp_path):
    cfg, sam = failed_first_run(tmp_path)
    assert state.read(cfg, "cleanup")["done"] == ["larcv_def"]

    result = clean_definitions(cfg, [RECO1, LARCV], sam)

    assert result["ok"] is True
    assert sam.retire_calls == ["reco1_101.root", "reco1_201.root"]
    assert state.read(cfg, "cleanup")["finished"] is True


def test_rerun_applies_the_saved_plan_instead_of_planning_again(tmp_path):
    cfg, sam = failed_first_run(tmp_path)
    # A fresh plan would keep this new lowest process id and retire worker 100.
    sam.add("reco1_def", "reco1_099.root", "gen_1.root", 99)
    sam.add("larcv_def", "larcv_099.root", "gen_1.root", 99)

    clean_definitions(cfg, [RECO1, LARCV], sam)

    assert sam.retire_calls == ["reco1_101.root", "reco1_201.root"]


def test_dry_run_after_a_failure_leaves_the_saved_plan_and_progress_alone(tmp_path):
    cfg, sam = failed_first_run(tmp_path)
    with open(plan_path(cfg)) as f:
        saved = f.read()
    progress = state.read(cfg, "cleanup")

    result = clean_definitions(cfg, [RECO1, LARCV], sam, dry_run=True)

    assert result["ok"] is True
    with open(plan_path(cfg)) as f:
        assert f.read() == saved
    assert state.read(cfg, "cleanup") == progress
    assert sam.retire_calls == []


def test_finished_cleanup_is_planned_afresh_next_time(tmp_path):
    cfg = make_cfg(tmp_path)
    sam = duplicated_campaign()
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True
    sam.add("reco1_def", "reco1_102.root", "gen_1.root", 102)

    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True

    assert "reco1_102.root" in sam.retired


def test_unfinished_plan_for_other_definitions_is_not_applied(tmp_path):
    cfg, sam = failed_first_run(tmp_path)

    result = clean_definitions(cfg, [RECO1], sam)

    assert result["ok"] is False
    assert result["failure"]["error"] == (
        f"the unfinished plan {plan_path(cfg)} is for larcv_def, reco1_def, not reco1_def"
    )
    assert sam.retire_calls == []


def test_missing_saved_plan_is_reported_and_nothing_is_retired(tmp_path):
    cfg, sam = failed_first_run(tmp_path)
    os.rename(plan_path(cfg), plan_path(cfg) + ".moved")

    result = clean_definitions(cfg, [RECO1, LARCV], sam)

    assert result["ok"] is False
    assert "cannot read the saved plan" in result["failure"]["error"]
    assert sam.retire_calls == []


# --- verify ---

def test_verification_passes_on_a_correct_result(tmp_path):
    result = clean_definitions(make_cfg(tmp_path), [RECO1, LARCV], duplicated_campaign())

    assert result["ok"] is True
    assert result["bad_inputs"] == {}


def test_verification_passes_when_the_kept_worker_lacks_a_tier(tmp_path):
    sam = duplicated_campaign()
    sam.add("caf_flat_def", "caf_102.root", "gen_1.root", 102)

    result = clean_definitions(make_cfg(tmp_path), [RECO1, LARCV, {"name": "caf_flat_def", "depth": 1}], sam)

    assert "caf_102.root" in sam.retired
    assert result["ok"] is True


def test_two_live_files_for_an_input_fail_verification_naming_it():
    sam = duplicated_campaign()
    plan = plan_cleanup([RECO1, LARCV], sam)
    for f in ("larcv_101.root", "larcv_201.root", "reco1_201.root"):
        sam.retireFile(f)

    assert verify_plan(plan, sam) == {
        "gen_1.root": ["reco1_def: reco1_101.root is live but not kept"],
    }


def test_live_file_that_is_not_in_the_plan_fails_verification_naming_its_input(tmp_path):
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True
    with open(plan_path(cfg)) as f:
        plan = json.load(f)
    sam.add("reco1_def", "reco1_102.root", "gen_1.root", 102)
    sam.add("reco1_def", "reco1_orphan.root", None, 103)

    assert verify_plan(plan, sam) == {
        "gen_1.root": ["reco1_def: reco1_102.root is live but not in the plan"],
        "reco1_orphan.root": [
            "reco1_def: reco1_orphan.root is live but not in the plan (reco1_orphan.root has no parent)",
        ],
    }


def test_retired_kept_file_fails_verification_naming_its_input():
    sam = duplicated_campaign()
    plan = plan_cleanup([RECO1], sam)
    for f in ("reco1_101.root", "reco1_201.root", "reco1_200.root"):
        sam.retireFile(f)

    assert verify_plan(plan, sam) == {
        "gen_2.root": ["reco1_def: kept file reco1_200.root is not live"],
    }


def test_retired_parent_fails_verification_naming_the_input(tmp_path, caplog):
    sam = duplicated_campaign()
    sam.retired.add("gen_2.root")
    cfg = make_cfg(tmp_path)

    result = clean_definitions(cfg, [RECO1], sam)

    assert result["ok"] is False
    assert result["failure"] is None
    assert result["bad_inputs"] == {
        "gen_2.root": ["reco1_def: parent gen_2.root of kept file reco1_200.root is not live"],
    }
    assert "cleanup: verification failed for input gen_2.root" in caplog.text
    assert state.read(cfg, "cleanup")["finished"] is False


def test_parent_inside_a_cleaned_definition_is_checked_too():
    sam = two_level_campaign()
    sam.add(None, "gen_1.root", None)
    plan = plan_cleanup([RECO1, CAF], sam)
    for f in ("caf_200.root", "reco1_200.root", "reco1_100.root"):
        sam.retireFile(f)

    assert verify_plan(plan, sam) == {
        "gen_1.root": [
            "caf_def: parent reco1_100.root of kept file caf_100.root is not live",
            "reco1_def: kept file reco1_100.root is not live",
        ],
    }


def test_file_skipped_by_the_children_guard_is_left_in_place_and_passes_verification(tmp_path, caplog):
    caplog.set_level("INFO")
    sam = duplicated_campaign()
    sam.add_child_outside("reco1_101.root", "other_campaign.root")
    cfg = make_cfg(tmp_path)

    result = clean_definitions(cfg, [RECO1], sam)

    assert result["ok"] is True
    assert result["bad_inputs"] == {}
    assert "reco1_101.root" not in sam.retired
    assert result["skipped"] == [
        {"definition": "reco1_def", "input": "gen_1.root", "file": "reco1_101.root"},
    ]
    assert (
        "cleanup: reco1_101.root from reco1_def left in place, has children outside the plan (input gen_1.root)"
    ) in caplog.text
    assert state.read(cfg, "cleanup")["finished"] is True


def test_dry_run_lists_the_skipped_files_too(tmp_path):
    sam = duplicated_campaign()
    sam.add_child_outside("reco1_101.root", "other_campaign.root")

    dry = clean_definitions(make_cfg(tmp_path), [RECO1], sam, dry_run=True)

    assert dry["skipped"] == [{"definition": "reco1_def", "input": "gen_1.root", "file": "reco1_101.root"}]


def test_restart_after_a_partial_apply_plans_afresh_and_finishes(tmp_path):
    sam = two_level_campaign()
    sam.add(None, "gen_1.root", None)
    sam.fail_on_retire = {"reco1_200.root"}
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1, CAF], sam)["ok"] is False
    assert sam.retired == {"caf_200.root"}
    sam.fail_on_retire = set()

    # The README's restart: delete the `cleanup` section.
    os.remove(cfg["state_file"])
    result = clean_definitions(cfg, [RECO1, CAF], sam)

    assert result["ok"] is True
    assert sam.retired == {"caf_200.root", "reco1_200.root"}


def test_rerun_after_a_failed_verification_only_verifies_again(tmp_path):
    sam = duplicated_campaign()
    sam.retired.add("gen_2.root")
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is False
    sam.retire_calls.clear()
    sam.retired.discard("gen_2.root")

    result = clean_definitions(cfg, [RECO1], sam)

    assert result["ok"] is True
    assert sam.retire_calls == []
    assert state.read(cfg, "cleanup")["finished"] is True


def test_dry_run_does_not_verify(tmp_path):
    sam = duplicated_campaign()
    sam.retired.add("gen_2.root")

    result = clean_definitions(make_cfg(tmp_path), [RECO1], sam, dry_run=True)

    assert result["ok"] is True
    assert result["bad_inputs"] == {}


# --- command line ---

def test_command_line_dry_run_writes_the_plan_in_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sam = duplicated_campaign()

    code = duplicate_cleanup.main(["--dataset", "reco1_def", "--dataset", "larcv_def", "--dry-run"], sam=sam)

    assert code == 0
    with open(tmp_path / "cleanup_plan_duplicate_cleanup.json") as f:
        assert json.load(f) == plan_cleanup([RECO1, LARCV], duplicated_campaign())
    assert sam.retire_calls == []
    assert os.listdir(tmp_path) == ["cleanup_plan_duplicate_cleanup.json"]


def test_command_line_retires_and_verifies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sam = duplicated_campaign()

    assert duplicate_cleanup.main(["--dataset", "reco1_def"], sam=sam) == 0

    assert sam.retired == {"reco1_101.root", "reco1_201.root"}
    assert sam.location_removals == []


def test_command_line_exits_non_zero_on_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sam = duplicated_campaign()
    sam.fail_on_retire = {"reco1_101.root"}

    assert duplicate_cleanup.main(["--dataset", "reco1_def"], sam=sam) == 1


def test_parents_are_checked_in_batches(monkeypatch):
    monkeypatch.setattr(duplicate_cleanup, "LIVE_CHECK_BATCH_SIZE", 2)
    sam = FakeSam()
    for i in range(5):
        sam.add(None, f"gen_{i}.root", None)
        sam.add("reco1_def", f"reco1_{i}.root", f"gen_{i}.root", i)
    plan = plan_cleanup([RECO1], sam)

    assert verify_plan(plan, sam) == {}
    assert sam.dimension_queries == [
        "file_name gen_0.root,gen_1.root", "file_name gen_2.root,gen_3.root", "file_name gen_4.root",
    ]
