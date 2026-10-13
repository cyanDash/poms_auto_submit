import json
import os
import subprocess
import sys

import duplicate_cleanup
from duplicate_cleanup import plan_cleanup, plan_path, write_plan

RECO1 = {"name": "reco1_def", "depth": 1}
LARCV = {"name": "larcv_def", "depth": 1}
CAF = {"name": "caf_def", "depth": 2}


class FakeSam:
    """Stands in for samweb_client.SAMWebClient: definitions, metadata in
    the shape of docs/raw/sam_getMetadata_reco1.json, and children derived
    from the declared parents (docs/raw/sam_lineage.json)."""

    def __init__(self):
        self.definitions = {}
        self.metadata = {}
        self.extra_children = {}
        self.lineage_calls = []
        self.batch_sizes = []

    def add(self, definition, file_name, parent, process_id=None):
        md = {
            "file_name": file_name,
            "file_id": len(self.metadata) + 1,
            "Dataset.Tag": definition,
            "parents": [{"file_name": parent, "file_id": 0}] if parent else [],
        }
        if process_id is not None:
            md["process_id"] = process_id
        self.metadata[file_name] = md
        if definition is not None:
            self.definitions.setdefault(definition, []).append(file_name)

    def add_child_outside(self, parent, child):
        """A live child that belongs to no definition under cleanup."""
        self.extra_children.setdefault(parent, []).append(child)

    def listFiles(self, defname=None):
        return list(self.definitions.get(defname, []))

    def getMultipleMetadata(self, names):
        names = list(names)
        self.batch_sizes.append(len(names))
        return [self.metadata[n] for n in names if n in self.metadata]

    def getFileLineage(self, lineage, name):
        assert lineage == "children"
        self.lineage_calls.append(name)
        children = [
            n for n, md in self.metadata.items()
            if md["parents"] and md["parents"][0]["file_name"] == name
        ]
        children += self.extra_children.get(name, [])
        return [{"file_name": n, "file_id": 0} for n in children]


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
    plan = plan_cleanup([RECO1], sam)

    path = write_plan(cfg, plan)

    assert path == plan_path(cfg) == str(tmp_path / "campaign" / "cleanup_plan_my_stage.json")
    with open(path) as f:
        assert json.load(f) == plan
    assert [p.name for p in (tmp_path / "campaign").iterdir()] == ["cleanup_plan_my_stage.json"]


def test_module_imports_without_samweb_client():
    code = (
        "import sys; sys.modules['samweb_client'] = None; "
        f"sys.path.insert(0, {os.path.dirname(duplicate_cleanup.__file__)!r}); "
        "import duplicate_cleanup"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
