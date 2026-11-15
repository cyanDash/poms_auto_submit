import json
import logging
import os
import subprocess
import sys

import pytest

import duplicate_cleanup
import state
from duplicate_cleanup import clean_definitions, plan_cleanup, verify_plan
from helpers import FakeSam, duplicated_campaign

RECO1 = {"name": "x_reco1_sbnd", "depth": 1}
LARCV = {"name": "x_larcvreco1_sbnd", "depth": 1}
CAF = {"name": "x_caf_sbnd", "depth": 2}


def plan_path(cfg):
    return os.path.join(cfg["cache_dir"], "cleanup_plan_my_stage.json")


def retired(plan):
    return {d["name"]: d["retire"] for d in plan["definitions"]}


# --- kept worker ---

def test_one_worker_per_input_plans_no_retirement():
    sam = FakeSam()
    for i, pid in ((1, 100), (2, 200)):
        sam.add("x_reco1_sbnd", f"reco1_{i}.root", f"gen_{i}.root", pid)
        sam.add("x_larcvreco1_sbnd", f"larcv_{i}.root", f"gen_{i}.root", pid)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert retired(plan) == {"x_larcvreco1_sbnd": [], "x_reco1_sbnd": []}
    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "x_larcvreco1_sbnd": ["larcv_1.root"], "x_reco1_sbnd": ["reco1_1.root"],
    }
    assert plan["inputs"]["gen_1.root"]["fallback"] is False
    assert sam.lineage_calls == []


def test_complete_worker_is_kept_in_every_tier_over_a_partial_one():
    sam = FakeSam()
    # The partial worker has the lower process id and the first file name.
    sam.add("x_larcvreco1_sbnd", "larcv_a.root", "gen_1.root", 100)
    sam.add("x_larcvreco1_sbnd", "larcv_b.root", "gen_1.root", 200)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 200
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "x_larcvreco1_sbnd": ["larcv_b.root"], "x_reco1_sbnd": ["reco1_b.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {"x_larcvreco1_sbnd": ["larcv_a.root"]}
    assert retired(plan) == {
        "x_larcvreco1_sbnd": [{"file": "larcv_a.root", "input": "gen_1.root", "process_id": 100}],
        "x_reco1_sbnd": [],
    }


def test_only_worker_being_partial_is_kept():
    sam = FakeSam()
    sam.add("x_larcvreco1_sbnd", "larcv_a.root", "gen_1.root", 100)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert plan["inputs"]["gen_1.root"]["kept"] == {"x_larcvreco1_sbnd": ["larcv_a.root"]}
    assert retired(plan) == {"x_larcvreco1_sbnd": [], "x_reco1_sbnd": []}


def test_tie_goes_to_the_lowest_process_id_on_every_run():
    def build(pids):
        sam = FakeSam()
        for pid in pids:
            sam.add("x_reco1_sbnd", f"reco1_{pid}.root", "gen_1.root", pid)
            sam.add("x_larcvreco1_sbnd", f"larcv_{pid}.root", "gen_1.root", pid)
        return sam

    first = plan_cleanup([RECO1, LARCV], build([300, 200]))
    second = plan_cleanup([LARCV, RECO1], build([200, 300]))

    assert first["inputs"]["gen_1.root"]["kept_process_id"] == 200
    assert [r["file"] for r in retired(first)["x_reco1_sbnd"]] == ["reco1_300.root"]
    assert [r["file"] for r in retired(first)["x_larcvreco1_sbnd"]] == ["larcv_300.root"]
    assert json.dumps(first) == json.dumps(second)


def test_a_single_copy_in_a_tier_the_kept_worker_lacks_is_kept_and_the_input_is_mixed():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_larcvreco1_sbnd", "larcv_a.root", "gen_1.root", 100)
    sam.add("x_flatcaf_sbnd", "caf_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1, LARCV, {"name": "x_flatcaf_sbnd", "depth": 1}], sam)

    entry = plan["inputs"]["gen_1.root"]
    assert entry["kept_process_id"] == 100
    assert entry["kept"]["x_flatcaf_sbnd"] == ["caf_b.root"]
    assert entry["retired"] == {}
    assert entry["mixed_workers"] is True
    assert entry["rescued_from"] == {"x_flatcaf_sbnd": [200]}
    assert retired(plan) == {"x_flatcaf_sbnd": [], "x_larcvreco1_sbnd": [], "x_reco1_sbnd": []}


def test_input_with_one_worker_is_not_mixed():
    plan = plan_cleanup([RECO1, LARCV], duplicated_campaign())

    assert all(i["mixed_workers"] is False for i in plan["inputs"].values())
    assert all("rescued_from" not in i for i in plan["inputs"].values())


def test_torch_triton_stage_retires_nothing():
    names = ("detsim_torch", "detsim_triton", "torch_triton_comparison_hist", "torch_torch_repeat_comparison_hist")
    sam = FakeSam()
    for name in names[:3]:
        sam.add(name, f"{name}_a.root", "gen_1.root", 1000)
    sam.add(names[3], f"{names[3]}_b.root", "gen_1.root", 2000)

    plan = plan_cleanup([{"name": n, "depth": 1} for n in names], sam)

    assert all(not rows for rows in retired(plan).values())
    assert plan["inputs"]["gen_1.root"]["mixed_workers"] is True
    assert plan["inputs"]["gen_1.root"]["rescued_from"] == {names[3]: [2000]}
    assert sum(len(f) for f in plan["inputs"]["gen_1.root"]["kept"].values()) == 4


def test_only_one_file_is_rescued_from_the_next_best_worker_and_the_rest_retired():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_larcvreco1_sbnd", "larcv_a.root", "gen_1.root", 100)
    sam.add("x_flatcaf_sbnd", "caf_z.root", "gen_1.root", 300)
    sam.add("x_flatcaf_sbnd", "caf_y.root", "gen_1.root", 300)
    sam.add("x_flatcaf_sbnd", "caf_x.root", "gen_1.root", 400)
    sam.add("x_larcvreco1_sbnd", "larcv_x.root", "gen_1.root", 400)

    plan = plan_cleanup([RECO1, LARCV, {"name": "x_flatcaf_sbnd", "depth": 1}], sam)

    entry = plan["inputs"]["gen_1.root"]
    # Worker 400 covers two definitions, 300 one: 400 is next best for x_flatcaf_sbnd.
    assert entry["kept"]["x_flatcaf_sbnd"] == ["caf_x.root"]
    assert entry["rescued_from"] == {"x_flatcaf_sbnd": [400]}
    assert entry["retired"] == {"x_flatcaf_sbnd": ["caf_y.root", "caf_z.root"], "x_larcvreco1_sbnd": ["larcv_x.root"]}


def test_rescue_under_a_kept_parent_in_a_chained_stage():
    sam = chained_campaign()
    sam.add("y_caf_sbnd", "caf2_x.root", "reco1_a.root", 500)

    plan = plan_cleanup([RECO1, CAF, {"name": "y_caf_sbnd", "depth": 2}], sam)

    entry = plan["inputs"]["gen_1.root"]
    assert entry["kept"]["y_caf_sbnd"] == ["caf2_x.root"]
    assert entry["kept"]["x_caf_sbnd"] == ["caf_a.root"]
    assert entry["rescued_from"] == {"y_caf_sbnd": [500]}
    assert entry["mixed_workers"] is True
    assert entry["retired"] == {"x_caf_sbnd": ["caf_b.root"], "x_reco1_sbnd": ["reco1_b.root"]}


def test_a_rescued_input_verifies_after_it_is_applied(tmp_path):
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_larcvreco1_sbnd", "larcv_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert verify_plan(plan, sam) == {}


# --- fallback ---

def test_no_process_id_keeps_first_file_by_name_per_definition():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root")
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root")
    sam.add("x_larcvreco1_sbnd", "larcv_z.root", "gen_1.root")
    sam.add("x_reco1_sbnd", "reco1_c.root", "gen_2.root", 100)

    plan = plan_cleanup([RECO1, LARCV], sam)

    assert plan["fallback_used"] is True
    assert plan["inputs"]["gen_1.root"]["fallback"] is True
    assert plan["inputs"]["gen_1.root"]["kept_process_id"] is None
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "x_larcvreco1_sbnd": ["larcv_z.root"], "x_reco1_sbnd": ["reco1_a.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {"x_reco1_sbnd": ["reco1_b.root"]}
    assert plan["inputs"]["gen_2.root"]["fallback"] is False


def test_one_file_without_process_id_puts_the_whole_input_on_fallback():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root")
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1], sam)

    assert plan["inputs"]["gen_1.root"]["fallback"] is True
    assert plan["inputs"]["gen_1.root"]["kept"] == {"x_reco1_sbnd": ["reco1_a.root"]}


def test_fallback_is_not_flagged_when_every_file_has_a_process_id():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)

    assert plan_cleanup([RECO1], sam)["fallback_used"] is False


# --- children guard ---

def test_file_with_a_live_child_outside_the_plan_is_skipped():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)
    sam.add_child_outside("reco1_b.root", "other_campaign.root")

    plan = plan_cleanup([RECO1], sam)

    reason = "has children that are not being retired: other_campaign.root"
    assert retired(plan) == {"x_reco1_sbnd": []}
    assert plan["definitions"][0]["skipped"] == [
        {"file": "reco1_b.root", "input": "gen_1.root", "process_id": 200, "reason": reason},
    ]
    assert plan["inputs"]["gen_1.root"]["skipped"] == {
        "x_reco1_sbnd": [{"file": "reco1_b.root", "reason": reason}],
    }


def test_empty_child_list_means_no_children():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)

    plan = plan_cleanup([RECO1], sam)

    assert sam.getFileLineage("children", "reco1_b.root") == []
    assert [r["file"] for r in retired(plan)["x_reco1_sbnd"]] == ["reco1_b.root"]


def test_children_are_looked_up_only_for_files_to_retire():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)
    sam.add("x_reco1_sbnd", "reco1_c.root", "gen_2.root", 300)

    plan_cleanup([RECO1], sam)

    assert sam.lineage_calls == ["reco1_b.root"]


# --- depth ---

def two_level_campaign():
    """caf files are children of the reco1 file of the same worker."""
    sam = FakeSam()
    for pid in (100, 200):
        sam.add("x_reco1_sbnd", f"reco1_{pid}.root", "gen_1.root", pid)
        sam.add("x_caf_sbnd", f"caf_{pid}.root", f"reco1_{pid}.root", pid)
    return sam


def test_definitions_are_listed_deepest_first():
    deeper = {"name": "a_histreco2_sbnd", "depth": 3}

    plan = plan_cleanup([RECO1, deeper, CAF, LARCV], FakeSam())

    assert [(d["name"], d["depth"]) for d in plan["definitions"]] == [
        ("a_histreco2_sbnd", 3), ("x_caf_sbnd", 2), ("x_larcvreco1_sbnd", 1), ("x_reco1_sbnd", 1),
    ]


def test_depth_two_file_groups_under_its_root_ancestor():
    plan = plan_cleanup([RECO1, CAF], two_level_campaign())

    assert list(plan["inputs"]) == ["gen_1.root"]
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "x_caf_sbnd": ["caf_100.root"], "x_reco1_sbnd": ["reco1_100.root"],
    }


def test_parent_is_retired_when_its_child_is_retired_too():
    plan = plan_cleanup([RECO1, CAF], two_level_campaign())

    assert [r["file"] for r in retired(plan)["x_caf_sbnd"]] == ["caf_200.root"]
    assert [r["file"] for r in retired(plan)["x_reco1_sbnd"]] == ["reco1_200.root"]


def test_parent_of_a_skipped_child_is_skipped():
    sam = two_level_campaign()
    sam.add_child_outside("caf_200.root", "other_campaign.root")

    plan = plan_cleanup([RECO1, CAF], sam)

    assert retired(plan) == {"x_caf_sbnd": [], "x_reco1_sbnd": []}
    assert [s["file"] for s in plan["definitions"][1]["skipped"]] == ["reco1_200.root"]


def test_depth_two_ancestor_outside_the_definitions_is_fetched():
    sam = FakeSam()
    for pid in (100, 200):
        sam.add(None, f"reco1_{pid}.root", "gen_1.root", pid)
        sam.add("x_caf_sbnd", f"caf_{pid}.root", f"reco1_{pid}.root", pid)

    plan = plan_cleanup([CAF], sam)

    assert plan["inputs"]["gen_1.root"]["retired"] == {"x_caf_sbnd": ["caf_200.root"]}


# --- chained stages: deeper tiers written by other jobs ---

def chained_campaign():
    """Two reco1 jobs for one input, each followed by its own caf job."""
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)
    sam.add("x_caf_sbnd", "caf_a.root", "reco1_a.root", 300)
    sam.add("x_caf_sbnd", "caf_b.root", "reco1_b.root", 400)
    return sam


def test_one_chain_is_kept_and_the_other_retired():
    plan = plan_cleanup([RECO1, CAF], chained_campaign())

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "x_caf_sbnd": ["caf_a.root"], "x_reco1_sbnd": ["reco1_a.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {
        "x_caf_sbnd": ["caf_b.root"], "x_reco1_sbnd": ["reco1_b.root"],
    }
    assert plan["fallback_used"] is False


def test_chain_with_more_tiers_is_kept_over_a_lower_process_id():
    sam = chained_campaign()
    sam.retired.add("caf_a.root")

    plan = plan_cleanup([RECO1, CAF], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 200
    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "x_caf_sbnd": ["caf_b.root"], "x_reco1_sbnd": ["reco1_b.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {"x_reco1_sbnd": ["reco1_a.root"]}


def test_duplicates_under_a_kept_parent_keep_one_worker():
    sam = chained_campaign()
    sam.add("x_caf_sbnd", "caf_a_again.root", "reco1_a.root", 250)

    plan = plan_cleanup([RECO1, CAF], sam)

    assert plan["inputs"]["gen_1.root"]["kept"]["x_caf_sbnd"] == ["caf_a_again.root"]
    assert plan["inputs"]["gen_1.root"]["retired"] == {
        "x_caf_sbnd": ["caf_a.root", "caf_b.root"], "x_reco1_sbnd": ["reco1_b.root"],
    }


def test_three_levels_keep_one_chain():
    sam = chained_campaign()
    sam.add("x_histreco2_sbnd", "hist_a.root", "caf_a.root", 500)
    sam.add("x_histreco2_sbnd", "hist_b.root", "caf_b.root", 50)

    plan = plan_cleanup([RECO1, CAF, {"name": "x_histreco2_sbnd", "depth": 3}], sam)

    assert plan["inputs"]["gen_1.root"]["kept"] == {
        "x_caf_sbnd": ["caf_a.root"], "x_histreco2_sbnd": ["hist_a.root"], "x_reco1_sbnd": ["reco1_a.root"],
    }
    assert plan["inputs"]["gen_1.root"]["retired"] == {
        "x_histreco2_sbnd": ["hist_b.root"], "x_caf_sbnd": ["caf_b.root"], "x_reco1_sbnd": ["reco1_b.root"],
    }


def test_chained_campaign_is_cleaned_and_verified(tmp_path):
    sam = chained_campaign()

    result = clean_definitions(make_cfg(tmp_path), [RECO1, CAF], sam)

    assert result["ok"] is True
    assert sam.retire_calls == ["caf_b.root", "reco1_b.root"]
    assert (
        "cleanup: retired caf_b.root from x_caf_sbnd (input gen_1.root, parent reco1_b.root is retired)"
    ) in cleanup_log(tmp_path)
    assert (
        "cleanup: retired reco1_b.root from x_reco1_sbnd (input gen_1.root, kept process id 100)"
    ) in cleanup_log(tmp_path)


def test_file_under_an_already_retired_parent_is_retired_not_promoted():
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)
    sam.add("x_caf_sbnd", "caf_b.root", "reco1_b.root", 50)
    sam.add("x_histreco2_sbnd", "hist_b.root", "caf_b.root", 40)
    sam.retired.add("reco1_b.root")

    plan = plan_cleanup([RECO1, CAF, {"name": "x_histreco2_sbnd", "depth": 3}], sam)

    assert plan["inputs"]["gen_1.root"]["kept_process_id"] == 100
    assert plan["inputs"]["gen_1.root"]["kept"] == {"x_reco1_sbnd": ["reco1_a.root"]}
    assert plan["inputs"]["gen_1.root"]["retired"] == {}
    # SAM cannot read a retired file's parents, so the input is unknown:
    # the files are planned under the retired ancestor's name.
    assert plan["inputs"]["reco1_b.root"]["kept"] == {}
    assert plan["inputs"]["reco1_b.root"]["retired"] == {"x_histreco2_sbnd": ["hist_b.root"], "x_caf_sbnd": ["caf_b.root"]}
    by_name = {d["name"]: d for d in plan["definitions"]}
    assert by_name["x_caf_sbnd"]["retire"] == [{
        "file": "caf_b.root", "input": "reco1_b.root", "process_id": 50,
        "reason": "parent reco1_b.root is retired",
    }]
    assert by_name["x_histreco2_sbnd"]["retire"][0]["reason"] == "parent caf_b.root is retired"
    assert all(d["ungrouped"] == [] for d in plan["definitions"])


def test_file_under_a_retired_ancestor_at_no_cleaned_tier_is_ungrouped_not_retired():
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    sam.add(None, "reco1_b.root", "gen_1.root", 200)
    sam.add(None, "reco2_b.root", "reco1_b.root", 200)
    sam.add("x_histreco2_sbnd", "hist_b.root", "reco2_b.root", 40)
    sam.retired.add("reco1_b.root")

    plan = plan_cleanup([{"name": "x_histreco2_sbnd", "depth": 3}], sam)

    assert plan["definitions"][0]["retire"] == []
    assert plan["definitions"][0]["ungrouped"] == [{
        "file": "hist_b.root",
        "reason": "its ancestor reco1_b.root is not in SAM and is at no cleaned definition's depth (1)",
    }]


def test_file_under_a_retired_ancestor_two_tiers_up_is_retired():
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)
    sam.add(None, "reco2_b.root", "reco1_b.root", 200)
    sam.add("x_histreco2_sbnd", "hist_b.root", "reco2_b.root", 40)
    sam.retired.add("reco1_b.root")

    plan = plan_cleanup([RECO1, {"name": "x_histreco2_sbnd", "depth": 3}], sam)

    assert plan["definitions"][0]["retire"] == [{
        "file": "hist_b.root", "input": "reco1_b.root", "process_id": 40,
        "reason": "ancestor reco1_b.root is retired",
    }]


def test_ancestor_left_out_of_a_bulk_read_but_readable_by_name_is_not_taken_as_retired():
    sam = chained_campaign()
    bulk = sam.getMultipleMetadata
    sam.getMultipleMetadata = lambda names: [md for md in bulk(names) if md["file_name"] != "reco1_b.root"]

    plan = plan_cleanup([CAF], sam)

    assert plan["inputs"]["gen_1.root"]["kept"] == {"x_caf_sbnd": ["caf_a.root"]}
    assert plan["inputs"]["gen_1.root"]["retired"] == {"x_caf_sbnd": ["caf_b.root"]}


def test_error_reading_an_ancestor_by_name_fails_the_plan():
    sam = chained_campaign()
    sam.retired.add("reco1_b.root")

    def broken(name):
        raise RuntimeError("503 Service Unavailable")

    sam.getMetadata = broken

    with pytest.raises(RuntimeError, match="503"):
        plan_cleanup([RECO1, CAF], sam)


def test_input_whose_only_files_hang_under_a_retired_parent_is_still_planned(tmp_path):
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)
    sam.add("x_caf_sbnd", "caf_b.root", "reco1_b.root", 50)
    sam.retired.add("reco1_b.root")

    result = clean_definitions(make_cfg(tmp_path), [RECO1, CAF], sam)

    assert result["ok"] is True
    assert sam.retired == {"reco1_b.root", "caf_b.root"}


def test_recorded_depth_that_stops_at_a_retired_output_file_fails_the_cleanup(tmp_path):
    sam = chained_campaign()
    sam.retired.add("reco1_b.root")

    result = clean_definitions(make_cfg(tmp_path), [RECO1, {"name": "x_caf_sbnd", "depth": 1}], sam)

    # caf_b's parent cannot be read, so it cannot be told from an input;
    # its sibling under the live reco1_a still stops the cleanup.
    assert result["ok"] is False
    assert "caf_a.root" in result["failure"]["error"]
    assert sam.retire_calls == []


# --- children guard: retired children ---

def test_retired_child_does_not_block_its_parent():
    sam = two_level_campaign()
    sam.retired.add("caf_200.root")

    plan = plan_cleanup([RECO1, CAF], sam)

    assert [r["file"] for r in retired(plan)["x_reco1_sbnd"]] == ["reco1_200.root"]
    assert plan["definitions"][1]["skipped"] == []


def test_file_retired_while_planning_counts_as_having_no_children():
    sam = duplicated_campaign()
    lineage = sam.getFileLineage

    def retired_meanwhile(kind, name):
        sam.retired.add(name)
        return lineage(kind, name)

    sam.getFileLineage = retired_meanwhile

    plan = plan_cleanup([RECO1], sam)

    assert [r["file"] for r in retired(plan)["x_reco1_sbnd"]] == ["reco1_101.root", "reco1_201.root"]
    assert plan["definitions"][0]["skipped"] == []


def test_other_error_reading_children_fails_the_plan():
    sam = duplicated_campaign()

    def broken(kind, name):
        raise RuntimeError("503 Service Unavailable")

    sam.getFileLineage = broken

    with pytest.raises(RuntimeError, match="503"):
        plan_cleanup([RECO1], sam)


# --- grouping ---

def test_data_file_hashed_stream_suffix_is_stripped():
    sam = FakeSam()
    raw = "data_EventBuilder3_p2_run18250_10_strmBNBZeroBias"
    sam.add("x_reco1_sbnd", "reco1_a.root", f"{raw}-0a1b2c3d.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", f"{raw}-9f8e7d6c.root", 200)
    sam.add("x_reco1_sbnd", "reco1_c.root", "data_EventBuilder3_p2_run18250_11_strmBNBZeroBias-11.root", 300)

    plan = plan_cleanup([RECO1], sam)

    assert sorted(plan["inputs"]) == [
        "data_EventBuilder3_p2_run18250_10_strmBNBZeroBias",
        "data_EventBuilder3_p2_run18250_11_strmBNBZeroBias",
    ]
    assert [r["file"] for r in retired(plan)["x_reco1_sbnd"]] == ["reco1_b.root"]


def test_mc_input_names_with_dashes_are_kept_whole():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "reco1-detsim-g4-gen-Gen2_2026-d00c.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "reco1-detsim-g4-gen-Gen2_2026-d00d.root", 200)

    plan = plan_cleanup([RECO1], sam)

    assert sorted(plan["inputs"]) == [
        "reco1-detsim-g4-gen-Gen2_2026-d00c.root", "reco1-detsim-g4-gen-Gen2_2026-d00d.root",
    ]
    assert retired(plan) == {"x_reco1_sbnd": []}


def test_file_that_cannot_be_grouped_is_reported_and_not_retired():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_orphan.root", None, 200)
    sam.definitions["x_reco1_sbnd"].append("reco1_unknown.root")

    plan = plan_cleanup([RECO1], sam)

    assert plan["definitions"][0]["ungrouped"] == [
        {"file": "reco1_orphan.root", "reason": "reco1_orphan.root has no parent"},
        {"file": "reco1_unknown.root", "reason": "no metadata in SAM for reco1_unknown.root"},
    ]
    assert retired(plan) == {"x_reco1_sbnd": []}


def test_file_whose_recorded_depth_stops_at_an_output_file_is_ungrouped():
    too_shallow = {"name": "x_caf_sbnd", "depth": 1}

    plan = plan_cleanup([RECO1, too_shallow], chained_campaign())

    by_name = {d["name"]: d for d in plan["definitions"]}
    assert [u["file"] for u in by_name["x_caf_sbnd"]["ungrouped"]] == ["caf_a.root", "caf_b.root"]
    assert "depth 1 does not lead to an input" in by_name["x_caf_sbnd"]["ungrouped"][0]["reason"]


def ungrouped_campaign():
    sam = duplicated_campaign()
    sam.add("x_reco1_sbnd", "reco1_orphan.root", None, 103)
    sam.definitions["x_reco1_sbnd"].append("reco1_unknown.root")
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
    assert state.read(cfg, "cleanup") is None
    with open(result["plan_path"]) as f:
        assert len(json.load(f)["definitions"][1]["ungrouped"]) == 2


def test_wrong_recorded_depth_fails_the_cleanup(tmp_path):
    sam = chained_campaign()

    result = clean_definitions(make_cfg(tmp_path), [RECO1, {"name": "x_caf_sbnd", "depth": 1}], sam)

    assert result["ok"] is False
    assert "caf_a.root" in result["failure"]["error"]
    assert sam.retire_calls == []


def test_cleanup_is_planned_afresh_once_the_ungrouped_file_is_gone(tmp_path):
    sam = ungrouped_campaign()
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is False
    sam.definitions["x_reco1_sbnd"].remove("reco1_orphan.root")
    sam.definitions["x_reco1_sbnd"].remove("reco1_unknown.root")

    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True

    assert sam.retired == {"reco1_101.root", "reco1_201.root"}


# --- bulk reads and totals ---

def test_metadata_is_read_in_batches(monkeypatch):
    monkeypatch.setattr(duplicate_cleanup, "METADATA_BATCH_SIZE", 2)
    sam = FakeSam()
    for i in range(5):
        sam.add("x_reco1_sbnd", f"reco1_{i}.root", f"gen_{i}.root", i)

    plan = plan_cleanup([RECO1], sam)

    # Only the output files: the inputs themselves are not read.
    assert sam.batch_sizes == [2, 2, 1]
    assert len(plan["inputs"]) == 5


def test_totals_per_definition():
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)
    sam.add("x_reco1_sbnd", "reco1_c.root", "gen_1.root", 300)
    sam.add("x_reco1_sbnd", "reco1_d.root", "gen_2.root", 400)
    sam.add("x_reco1_sbnd", "reco1_orphan.root", None, 500)
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
        "campaign_stage_name": "my_stage",
    }
    sam = FakeSam()
    sam.add("x_reco1_sbnd", "reco1_a.root", "gen_1.root", 100)
    sam.add("x_reco1_sbnd", "reco1_b.root", "gen_1.root", 200)

    result = clean_definitions(cfg, [RECO1], sam, dry_run=True)

    assert result["plan_path"] == str(tmp_path / "campaign" / "cleanup_plan_my_stage.json")
    with open(result["plan_path"]) as f:
        assert json.load(f) == plan_cleanup([RECO1], sam)
    assert sorted(p.name for p in (tmp_path / "campaign").iterdir()) == [
        "cleanup_plan_my_stage.json", "my_stage_cleanup.log", "x_reco1_sbnd_retired_files.txt",
    ]


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
        "campaign_stage_name": "my_stage",
    }


def cleanup_log(tmp_path):
    return (tmp_path / "my_stage_cleanup.log").read_text()


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


def test_each_retirement_is_logged_with_its_input_and_kept_process_id(tmp_path):
    clean_definitions(make_cfg(tmp_path), [RECO1], duplicated_campaign())

    lines = [line for line in cleanup_log(tmp_path).splitlines() if "retired reco1_201.root" in line]
    assert len(lines) == 1
    assert lines[0].endswith(
        "INFO cleanup: retired reco1_201.root from x_reco1_sbnd (input gen_2.root, kept process id 200)"
    )


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
        "definition": "x_caf_sbnd", "file": "caf_200.root",
        "error": "500 Server Error retiring caf_200.root",
    }
    assert sam.retire_calls == ["caf_200.root"]
    assert sam.retired == set()
    assert "cleanup: retiring caf_200.root from x_caf_sbnd failed" in caplog.text


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
    assert "cleanup: reco1_101.root from x_reco1_sbnd is already retired" in cleanup_log(tmp_path)


# --- list of the retired files' full paths ---

def retired_list(tmp_path, definition="x_reco1_sbnd"):
    return tmp_path / f"{definition}_retired_files.txt"


def test_full_paths_are_listed_before_the_first_retirement(tmp_path, caplog):
    caplog.set_level("INFO")
    sam = duplicated_campaign()
    sam.locations["reco1_201.root"] = ["enstore:/pnfs/sbnd/tape/reco1"]
    seen = []
    retire = sam.retireFile

    def retire_and_look(name):
        seen.append(retired_list(tmp_path).read_text())
        retire(name)

    sam.retireFile = retire_and_look

    result = clean_definitions(make_cfg(tmp_path), [RECO1], sam)

    expected = "/pnfs/sbnd/fake/x_reco1_sbnd/reco1_101.root\n/pnfs/sbnd/tape/reco1/reco1_201.root\n"
    assert result["ok"] is True
    assert seen == [expected, expected]
    assert retired_list(tmp_path).read_text() == expected
    assert result["retired_files_lists"] == [str(retired_list(tmp_path))]
    assert f"x_reco1_sbnd: full paths of the 2 file(s) to retire written to {retired_list(tmp_path)}" in cleanup_log(tmp_path)
    assert sam.location_removals == []


def test_definition_with_nothing_to_retire_gets_no_list(tmp_path):
    sam = duplicated_campaign()
    sam.add("x_flatcaf_sbnd", "caf_100.root", "gen_1.root", 100)

    result = clean_definitions(make_cfg(tmp_path), [RECO1, {"name": "x_flatcaf_sbnd", "depth": 1}], sam)

    assert not retired_list(tmp_path, "x_flatcaf_sbnd").exists()
    assert result["retired_files_lists"] == [str(retired_list(tmp_path))]


def test_file_without_a_location_is_listed_by_name_and_logged(tmp_path):
    sam = duplicated_campaign()
    sam.locations["reco1_101.root"] = []

    assert clean_definitions(make_cfg(tmp_path), [RECO1], sam)["ok"] is True

    assert retired_list(tmp_path).read_text() == "reco1_101.root\n/pnfs/sbnd/fake/x_reco1_sbnd/reco1_201.root\n"
    assert "cleanup: reco1_101.root from x_reco1_sbnd has no location in SAM" in cleanup_log(tmp_path)


def test_failure_to_read_a_location_retires_nothing_in_that_definition(tmp_path):
    sam = duplicated_campaign()
    sam.fail_on_locate = {"reco1_201.root"}

    result = clean_definitions(make_cfg(tmp_path), [RECO1, LARCV], sam)

    assert result["ok"] is False
    assert result["failure"] == {
        "definition": "x_reco1_sbnd", "file": None, "error": "500 Server Error locating reco1_201.root",
    }
    assert sam.retired == {"larcv_101.root", "larcv_201.root"}


def test_failure_to_write_the_list_retires_nothing_in_that_definition(tmp_path):
    sam = duplicated_campaign()
    retired_list(tmp_path).mkdir()

    result = clean_definitions(make_cfg(tmp_path), [RECO1], sam)

    assert result["ok"] is False
    assert result["failure"]["definition"] == "x_reco1_sbnd"
    assert sam.retire_calls == []


def test_resume_keeps_the_entries_already_listed(tmp_path):
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)
    sam.fail_on_retire = {"reco1_201.root"}
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is False
    sam.fail_on_retire = set()

    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True

    assert retired_list(tmp_path).read_text() == (
        "/pnfs/sbnd/fake/x_reco1_sbnd/reco1_101.root\n/pnfs/sbnd/fake/x_reco1_sbnd/reco1_201.root\n"
    )


def test_resume_without_the_list_names_the_files_already_retired(tmp_path):
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)
    sam.fail_on_retire = {"reco1_201.root"}
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is False
    sam.fail_on_retire = set()
    retired_list(tmp_path).unlink()

    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True

    assert retired_list(tmp_path).read_text() == "reco1_101.root\n/pnfs/sbnd/fake/x_reco1_sbnd/reco1_201.root\n"
    assert "cleanup: reco1_101.root from x_reco1_sbnd is not in SAM any more" in cleanup_log(tmp_path)


def test_no_sam_definition_is_created(tmp_path):
    sam = duplicated_campaign()
    sam.createDefinition = lambda *args, **kwargs: pytest.fail("createDefinition called")

    assert clean_definitions(make_cfg(tmp_path), [RECO1, LARCV], sam)["ok"] is True


def test_dry_run_writes_the_list_too(tmp_path):
    sam = duplicated_campaign()

    result = clean_definitions(make_cfg(tmp_path), [RECO1], sam, dry_run=True)

    assert retired_list(tmp_path).read_text() == (
        "/pnfs/sbnd/fake/x_reco1_sbnd/reco1_101.root\n/pnfs/sbnd/fake/x_reco1_sbnd/reco1_201.root\n"
    )
    assert result["retired_files_lists"] == [str(retired_list(tmp_path))]
    assert sam.retire_calls == []


# --- dry run ---

def test_dry_run_writes_the_plan_and_changes_nothing(tmp_path, caplog):
    caplog.set_level("INFO")
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)

    result = clean_definitions(cfg, [RECO1], sam, dry_run=True)

    assert result == {
        "ok": True, "plan_path": plan_path(cfg), "failure": None, "bad_inputs": {}, "skipped": [],
        "retired_files_lists": [str(tmp_path / "x_reco1_sbnd_retired_files.txt")],
    }
    with open(plan_path(cfg)) as f:
        assert json.load(f) == plan_cleanup([RECO1], duplicated_campaign())
    assert sam.retire_calls == []
    assert state.read(cfg, "cleanup") is None
    assert (
        "INFO [dry run] cleanup: would retire reco1_201.root from x_reco1_sbnd "
        "(input gen_2.root, kept process id 200)"
    ) in cleanup_log(tmp_path)


def test_real_run_writes_the_same_plan_as_a_dry_run(tmp_path):
    dry, real = make_cfg(tmp_path / "dry"), make_cfg(tmp_path / "real")

    clean_definitions(dry, [RECO1, LARCV], duplicated_campaign(), dry_run=True)
    clean_definitions(real, [RECO1, LARCV], duplicated_campaign())

    with open(plan_path(dry)) as f_dry, open(plan_path(real)) as f_real:
        assert json.load(f_dry) == json.load(f_real)


# --- resume ---

def failed_first_run(tmp_path):
    """x_larcvreco1_sbnd is cleaned, then x_reco1_sbnd fails on its first file."""
    sam = duplicated_campaign()
    sam.fail_on_retire = {"reco1_101.root"}
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1, LARCV], sam)["ok"] is False
    sam.fail_on_retire = set()
    sam.retire_calls.clear()
    return cfg, sam


def test_rerun_after_a_failure_skips_completed_definitions(tmp_path):
    cfg, sam = failed_first_run(tmp_path)
    assert state.read(cfg, "cleanup")["done"] == ["x_larcvreco1_sbnd"]

    result = clean_definitions(cfg, [RECO1, LARCV], sam)

    assert result["ok"] is True
    assert sam.retire_calls == ["reco1_101.root", "reco1_201.root"]
    assert state.read(cfg, "cleanup")["finished"] is True


def test_rerun_applies_the_saved_plan_instead_of_planning_again(tmp_path):
    cfg, sam = failed_first_run(tmp_path)
    # A fresh plan would keep this new lowest process id and retire worker 100.
    sam.add("x_reco1_sbnd", "reco1_099.root", "gen_1.root", 99)
    sam.add("x_larcvreco1_sbnd", "larcv_099.root", "gen_1.root", 99)

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
    sam.add("x_reco1_sbnd", "reco1_102.root", "gen_1.root", 102)

    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True

    assert "reco1_102.root" in sam.retired


def test_unfinished_plan_for_other_definitions_is_not_applied(tmp_path):
    cfg, sam = failed_first_run(tmp_path)

    result = clean_definitions(cfg, [RECO1], sam)

    assert result["ok"] is False
    assert result["failure"]["error"] == (
        f"the unfinished plan {plan_path(cfg)} is for x_larcvreco1_sbnd, x_reco1_sbnd, not x_reco1_sbnd"
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


def test_verification_passes_when_the_kept_worker_lacks_a_tier_and_its_single_copy_stays(tmp_path):
    sam = duplicated_campaign()
    sam.add("x_flatcaf_sbnd", "caf_102.root", "gen_1.root", 102)

    result = clean_definitions(make_cfg(tmp_path), [RECO1, LARCV, {"name": "x_flatcaf_sbnd", "depth": 1}], sam)

    assert "caf_102.root" not in sam.retired
    assert result["ok"] is True


def test_two_live_files_for_an_input_fail_verification_naming_it():
    sam = duplicated_campaign()
    plan = plan_cleanup([RECO1, LARCV], sam)
    for f in ("larcv_101.root", "larcv_201.root", "reco1_201.root"):
        sam.retireFile(f)

    assert verify_plan(plan, sam) == {
        "gen_1.root": ["x_reco1_sbnd: reco1_101.root is live but not kept"],
    }


def test_live_file_that_is_not_in_the_plan_fails_verification_naming_its_input(tmp_path):
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)
    assert clean_definitions(cfg, [RECO1], sam)["ok"] is True
    with open(plan_path(cfg)) as f:
        plan = json.load(f)
    sam.add("x_reco1_sbnd", "reco1_102.root", "gen_1.root", 102)
    sam.add("x_reco1_sbnd", "reco1_orphan.root", None, 103)

    assert verify_plan(plan, sam) == {
        "gen_1.root": ["x_reco1_sbnd: reco1_102.root is live but not in the plan"],
        "reco1_orphan.root": [
            "x_reco1_sbnd: reco1_orphan.root is live but not in the plan (reco1_orphan.root has no parent)",
        ],
    }


def test_retired_kept_file_fails_verification_naming_its_input():
    sam = duplicated_campaign()
    plan = plan_cleanup([RECO1], sam)
    for f in ("reco1_101.root", "reco1_201.root", "reco1_200.root"):
        sam.retireFile(f)

    assert verify_plan(plan, sam) == {
        "gen_2.root": ["x_reco1_sbnd: kept file reco1_200.root is not live"],
    }


def test_retired_parent_fails_verification_naming_the_input(tmp_path, caplog):
    sam = duplicated_campaign()
    sam.retired.add("gen_2.root")
    cfg = make_cfg(tmp_path)

    result = clean_definitions(cfg, [RECO1], sam)

    assert result["ok"] is False
    assert result["failure"]["definition"] == "x_reco1_sbnd"
    assert result["bad_inputs"] == {
        "gen_2.root": ["x_reco1_sbnd: parent gen_2.root of kept file reco1_200.root is not live"],
    }
    assert "cleanup: verification failed for 1 input(s)" in caplog.text
    assert (
        "cleanup: verification failed for input gen_2.root: "
        "x_reco1_sbnd: parent gen_2.root of kept file reco1_200.root is not live"
    ) in cleanup_log(tmp_path)
    assert state.read(cfg, "cleanup")["finished"] is False


def test_live_file_under_a_retired_parent_that_is_not_in_the_plan_fails_verification():
    sam = chained_campaign()
    plan = plan_cleanup([RECO1, CAF], sam)
    for f in ("caf_b.root", "reco1_b.root"):
        sam.retireFile(f)
    sam.add("x_caf_sbnd", "caf_late.root", "reco1_b.root", 500)

    assert verify_plan(plan, sam) == {
        "reco1_b.root": ["x_caf_sbnd: caf_late.root is live but not in the plan (parent reco1_b.root is retired)"],
    }


def test_parent_inside_a_cleaned_definition_is_checked_too():
    sam = two_level_campaign()
    sam.add(None, "gen_1.root", None)
    plan = plan_cleanup([RECO1, CAF], sam)
    for f in ("caf_200.root", "reco1_200.root", "reco1_100.root"):
        sam.retireFile(f)

    assert verify_plan(plan, sam) == {
        "gen_1.root": [
            "x_caf_sbnd: parent reco1_100.root of kept file caf_100.root is not live",
            "x_reco1_sbnd: kept file reco1_100.root is not live",
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
        {"definition": "x_reco1_sbnd", "input": "gen_1.root", "file": "reco1_101.root"},
    ]
    assert (
        "cleanup: reco1_101.root from x_reco1_sbnd left in place (input gen_1.root): "
        "has children that are not being retired: other_campaign.root"
    ) in cleanup_log(tmp_path)
    assert state.read(cfg, "cleanup")["finished"] is True


def test_dry_run_lists_the_skipped_files_too(tmp_path):
    sam = duplicated_campaign()
    sam.add_child_outside("reco1_101.root", "other_campaign.root")

    dry = clean_definitions(make_cfg(tmp_path), [RECO1], sam, dry_run=True)

    assert dry["skipped"] == [{"definition": "x_reco1_sbnd", "input": "gen_1.root", "file": "reco1_101.root"}]


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


# --- the cleanup log ---

DETAIL_LOGGER = logging.getLogger("duplicate_cleanup.detail")


def test_per_file_lines_go_to_the_cleanup_log_and_not_the_main_log(tmp_path, caplog):
    caplog.set_level("INFO")
    sam = duplicated_campaign()
    sam.add_child_outside("larcv_101.root", "other_campaign.root")

    assert clean_definitions(make_cfg(tmp_path), [RECO1, LARCV], sam)["ok"] is True

    main = [r.getMessage() for r in caplog.records]
    assert main == [
        f"cleanup: started for 2 definition(s) -- detailed log in {tmp_path}/my_stage_cleanup.log",
        f"cleanup: plan written to {tmp_path}/cleanup_plan_my_stage.json",
        "cleanup: x_larcvreco1_sbnd: 4 file(s), 2 kept, 1 retired, 1 left in place",
        "cleanup: x_reco1_sbnd: 4 file(s), 2 kept, 2 retired, 0 left in place",
        "cleanup: verified 2 input(s)",
    ]
    detail = cleanup_log(tmp_path)
    for line in main:
        assert line in detail
    assert "cleanup: x_larcvreco1_sbnd: start, 1 file(s) to retire" in detail
    assert "cleanup: retired larcv_201.root from x_larcvreco1_sbnd" in detail
    assert "cleanup: larcv_101.root from x_larcvreco1_sbnd left in place" in detail


def test_failure_is_in_both_logs(tmp_path, caplog):
    sam = duplicated_campaign()
    sam.fail_on_retire = {"reco1_101.root"}

    clean_definitions(make_cfg(tmp_path), [RECO1], sam)

    assert "cleanup: retiring reco1_101.root from x_reco1_sbnd failed" in caplog.text
    assert "cleanup: retiring reco1_101.root from x_reco1_sbnd failed" in cleanup_log(tmp_path)


def test_cleanup_log_is_appended_to_and_a_dry_run_is_marked(tmp_path):
    sam = duplicated_campaign()
    cfg = make_cfg(tmp_path)

    clean_definitions(cfg, [RECO1], sam, dry_run=True)
    clean_definitions(cfg, [RECO1], sam)

    lines = cleanup_log(tmp_path).splitlines()
    dry = [line for line in lines if "[dry run]" in line]
    assert any("cleanup: would retire reco1_101.root" in line for line in dry)
    assert all("[dry run]" in line for line in lines[:len(dry)])
    assert any(line.endswith("cleanup: retired reco1_101.root from x_reco1_sbnd (input gen_1.root, kept process id 100)")
               for line in lines[len(dry):])


@pytest.mark.parametrize("fail", [False, True])
def test_no_log_handler_is_left_behind(tmp_path, fail):
    sam = duplicated_campaign()
    if fail:
        sam.listFiles = None  # planning raises

    assert clean_definitions(make_cfg(tmp_path), [RECO1], sam)["ok"] is not fail

    assert DETAIL_LOGGER.handlers == []
    assert DETAIL_LOGGER.propagate is False


# --- command line ---

def test_command_line_dry_run_writes_the_plan_in_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sam = duplicated_campaign()

    code = duplicate_cleanup.main(["--dataset", "x_reco1_sbnd", "--dataset", "x_larcvreco1_sbnd", "--dry-run"], sam=sam)

    assert code == 0
    with open(tmp_path / "cleanup_plan_duplicate_cleanup.json") as f:
        assert json.load(f) == plan_cleanup([RECO1, LARCV], duplicated_campaign())
    assert sam.retire_calls == []
    assert sorted(os.listdir(tmp_path)) == [
        "cleanup_plan_duplicate_cleanup.json", "duplicate_cleanup_cleanup.log",
        "x_larcvreco1_sbnd_retired_files.txt", "x_reco1_sbnd_retired_files.txt",
    ]


def test_command_line_retires_and_verifies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sam = duplicated_campaign()

    assert duplicate_cleanup.main(["--dataset", "x_reco1_sbnd"], sam=sam) == 0

    assert sam.retired == {"reco1_101.root", "reco1_201.root"}
    assert sam.location_removals == []


def test_command_line_exits_non_zero_on_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sam = duplicated_campaign()
    sam.fail_on_retire = {"reco1_101.root"}

    assert duplicate_cleanup.main(["--dataset", "x_reco1_sbnd"], sam=sam) == 1


def test_parents_are_checked_in_batches(monkeypatch):
    monkeypatch.setattr(duplicate_cleanup, "LIVE_CHECK_BATCH_SIZE", 2)
    sam = FakeSam()
    for i in range(5):
        sam.add(None, f"gen_{i}.root", None)
        sam.add("x_reco1_sbnd", f"reco1_{i}.root", f"gen_{i}.root", i)
    plan = plan_cleanup([RECO1], sam)

    assert verify_plan(plan, sam) == {}
    assert sam.dimension_queries == [
        "file_name gen_0.root,gen_1.root", "file_name gen_2.root,gen_3.root", "file_name gen_4.root",
    ]


# --- tier order ---

T_RECO1 = {"name": "p_reco1_sbnd", "depth": 1}
T_CAF = {"name": "p_caf_sbnd", "depth": 1}
T_FLATCAF = {"name": "p_flatcaf_sbnd", "depth": 1}
T_HISTRECO2 = {"name": "p_histreco2_sbnd", "depth": 1}
T_LARCV = {"name": "p_larcvreco1_sbnd", "depth": 1}
T_MYSTERY = {"name": "p_mystery_sbnd", "depth": 1}


def tiered_campaign(*definitions):
    """Two workers per input, a file of each in every definition."""
    sam = FakeSam()
    sam.add(None, "gen_1.root", None)
    for pid in (100, 200):
        for d in definitions:
            sam.add(d["name"], f"{d['name']}_{pid}.root", "gen_1.root", pid)
    return sam


def test_definitions_follow_the_fixed_tier_order_youngest_first():
    plan = plan_cleanup([T_RECO1, T_CAF, T_FLATCAF, T_HISTRECO2, T_LARCV], FakeSam())

    assert [d["name"] for d in plan["definitions"]] == [
        "p_flatcaf_sbnd", "p_caf_sbnd", "p_histreco2_sbnd", "p_larcvreco1_sbnd", "p_reco1_sbnd",
    ]
    assert duplicate_cleanup.TIER_ORDER == ("flatcaf", "caf", "histreco2", "larcvreco1", "reco1")


def test_a_missing_tier_is_simply_absent_from_the_order():
    plan = plan_cleanup([T_RECO1, T_FLATCAF, T_CAF], FakeSam())

    assert [d["name"] for d in plan["definitions"]] == ["p_flatcaf_sbnd", "p_caf_sbnd", "p_reco1_sbnd"]


def test_depth_comes_before_tier_order():
    deep_reco1 = {"name": "q_reco1_sbnd", "depth": 2}

    plan = plan_cleanup([T_FLATCAF, deep_reco1, T_CAF], FakeSam())

    assert [d["name"] for d in plan["definitions"]] == ["q_reco1_sbnd", "p_flatcaf_sbnd", "p_caf_sbnd"]


def test_apply_retires_tier_by_tier_in_the_tier_order(tmp_path):
    sam = tiered_campaign(T_RECO1, T_CAF, T_FLATCAF)

    result = clean_definitions(make_cfg(tmp_path), [T_RECO1, T_CAF, T_FLATCAF], sam)

    assert result["ok"] is True
    assert sam.retire_calls == [
        "p_flatcaf_sbnd_200.root", "p_caf_sbnd_200.root", "p_reco1_sbnd_200.root",
    ]


def test_an_unknown_tier_is_cleaned_after_every_known_tier(tmp_path, caplog):
    sam = tiered_campaign(T_RECO1, T_MYSTERY, T_CAF)

    with caplog.at_level(logging.WARNING):
        result = clean_definitions(make_cfg(tmp_path), [T_MYSTERY, T_RECO1, T_CAF], sam)

    assert result["ok"] is True
    assert sam.retire_calls == [
        "p_caf_sbnd_200.root", "p_reco1_sbnd_200.root", "p_mystery_sbnd_200.root",
    ]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("p_mystery_sbnd" in w and "guess" in w for w in warnings)
    assert not any("p_reco1_sbnd" in w for w in warnings)


def test_a_deeper_unknown_tier_still_comes_first_and_unknown_ones_go_by_name():
    deep = {"name": "q_other_sbnd", "depth": 2}
    zed = {"name": "p_zed_sbnd", "depth": 1}
    ahead = {"name": "p_alpha", "depth": 1}

    plan = plan_cleanup([zed, T_RECO1, ahead, deep], FakeSam())

    assert [d["name"] for d in plan["definitions"]] == ["q_other_sbnd", "p_reco1_sbnd", "p_alpha", "p_zed_sbnd"]


def test_each_definition_is_verified_before_the_next_starts(tmp_path, monkeypatch):
    sam = tiered_campaign(T_RECO1, T_CAF, T_FLATCAF)
    order = []
    real_retire, real_verify = sam.retireFile, duplicate_cleanup.verify_plan

    def retire(name):
        order.append(("retire", name))
        real_retire(name)

    def verify(plan, sam_, names=None):
        order.append(("verify", tuple(names)))
        return real_verify(plan, sam_, names)

    sam.retireFile = retire
    monkeypatch.setattr(duplicate_cleanup, "verify_plan", verify)

    result = clean_definitions(make_cfg(tmp_path), [T_RECO1, T_CAF, T_FLATCAF], sam)

    assert result["ok"] is True
    assert order == [
        ("retire", "p_flatcaf_sbnd_200.root"), ("verify", ("p_flatcaf_sbnd",)),
        ("retire", "p_caf_sbnd_200.root"), ("verify", ("p_caf_sbnd",)),
        ("retire", "p_reco1_sbnd_200.root"), ("verify", ("p_reco1_sbnd",)),
    ]


def test_a_failed_verification_stops_before_the_next_tier_and_resume_continues(tmp_path):
    sam = tiered_campaign(T_RECO1, T_CAF, T_FLATCAF)
    cfg = make_cfg(tmp_path)
    real_retire = sam.retireFile

    def retire_then_a_stray_appears(name):
        real_retire(name)
        if name == "p_flatcaf_sbnd_200.root":
            sam.add("p_flatcaf_sbnd", "p_flatcaf_sbnd_stray.root", "gen_1.root", 300)

    sam.retireFile = retire_then_a_stray_appears

    result = clean_definitions(cfg, [T_RECO1, T_CAF, T_FLATCAF], sam)

    assert result["ok"] is False
    assert result["failure"]["definition"] == "p_flatcaf_sbnd"
    assert "verification" in result["failure"]["error"]
    assert result["bad_inputs"]
    assert sam.retire_calls == ["p_flatcaf_sbnd_200.root"]
    assert state.read(cfg, "cleanup")["done"] == []

    sam.retireFile = real_retire
    sam.metadata.pop("p_flatcaf_sbnd_stray.root")
    sam.definitions["p_flatcaf_sbnd"].remove("p_flatcaf_sbnd_stray.root")
    result = clean_definitions(cfg, [T_RECO1, T_CAF, T_FLATCAF], sam)

    assert result["ok"] is True
    assert sam.retire_calls[1:] == ["p_caf_sbnd_200.root", "p_reco1_sbnd_200.root"]
    assert state.read(cfg, "cleanup")["done"] == ["p_flatcaf_sbnd", "p_caf_sbnd", "p_reco1_sbnd"]


# --- merged outputs: several parents ---

def merged_campaign(parents_by_pid):
    """One flatcaf per worker, each with the given parents; the first parent
    is the same, so the files group together."""
    sam = FakeSam()
    for name in ("caf_a.root", "caf_b.root", "caf_c.root"):
        sam.add(None, name, None)
    for pid, parents in parents_by_pid.items():
        name = f"p_flatcaf_sbnd_{pid}.root"
        sam.add("p_flatcaf_sbnd", name, parents[0], pid)
        sam.metadata[name]["parents"] = [{"file_name": p, "file_id": 0} for p in parents]
    return sam


def test_files_with_different_parent_sets_are_left_in_place(tmp_path):
    sam = merged_campaign({100: ["caf_a.root", "caf_b.root"], 200: ["caf_a.root", "caf_c.root"]})
    cfg = make_cfg(tmp_path)

    plan = plan_cleanup([T_FLATCAF], sam)

    entry = plan["definitions"][0]
    assert entry["retire"] == []
    assert [(r["file"], r["reason"]) for r in entry["skipped"]] == [
        ("p_flatcaf_sbnd_200.root", "left in place: parent sets differ"),
    ]
    assert plan["inputs"]["caf_a.root"]["skipped"] == {
        "p_flatcaf_sbnd": [{"file": "p_flatcaf_sbnd_200.root", "reason": "left in place: parent sets differ"}],
    }

    result = clean_definitions(cfg, [T_FLATCAF], sam)

    assert result["ok"] is True
    assert sam.retire_calls == []
    assert "p_flatcaf_sbnd_200.root from p_flatcaf_sbnd (input caf_a.root): left in place: parent sets differ" in (
        cleanup_log(tmp_path)
    )


def test_identical_parent_sets_in_another_order_are_true_duplicates():
    sam = merged_campaign({
        100: ["caf_a.root", "caf_b.root", "caf_c.root"],
        200: ["caf_a.root", "caf_c.root", "caf_b.root"],
    })

    plan = plan_cleanup([T_FLATCAF], sam)

    assert [r["file"] for r in plan["definitions"][0]["retire"]] == ["p_flatcaf_sbnd_200.root"]
    assert plan["definitions"][0]["skipped"] == []


def test_a_group_with_differing_parent_sets_does_not_hold_back_other_groups():
    sam = merged_campaign({100: ["caf_a.root", "caf_b.root"], 200: ["caf_a.root", "caf_c.root"]})
    sam.add("p_flatcaf_sbnd", "p_flatcaf_sbnd_other_100.root", "caf_b.root", 100)
    sam.add("p_flatcaf_sbnd", "p_flatcaf_sbnd_other_200.root", "caf_b.root", 200)

    plan = plan_cleanup([T_FLATCAF], sam)

    assert [r["file"] for r in plan["definitions"][0]["retire"]] == ["p_flatcaf_sbnd_other_200.root"]
    assert [r["file"] for r in plan["definitions"][0]["skipped"]] == ["p_flatcaf_sbnd_200.root"]
