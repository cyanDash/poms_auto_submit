import json
import logging
from pathlib import Path

import pytest

import state
from helpers import FileNotFound, make_cfg
from output_definitions import OutputDefinitionsError, get_output_definitions

RAW = Path(__file__).resolve().parent.parent / "docs" / "raw"


class NoCallSession:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected POMS call: {name}")


class NoCallSam:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected SAM call: {name}")


def make_state_cfg(tmp_path, **overrides):
    return make_cfg(state_file=str(tmp_path / "state.json"), **overrides)


def recorded(tmp_path):
    return state.read({"state_file": str(tmp_path / "state.json")}, "output_definitions")


# --- section already filled ---

def test_filled_section_is_returned_without_any_poms_or_sam_call(tmp_path):
    entries = [{"name": "ds_reco1", "depth": 1}, {"name": "ds_caf", "depth": 2}]
    (tmp_path / "state.json").write_text(json.dumps({"output_definitions": entries}))

    assert get_output_definitions(make_state_cfg(tmp_path), NoCallSession(), NoCallSam()) == entries


# --- inference: fakes built from docs/raw ---

POSTSCRIPT = RAW / "metadata_postscripts_MC_scrub_detsim_reco1_reco2_caf.sh"
REAL_POSTSCRIPT_PATH = (
    "/exp/sbnd/app/users/sbndpro/mcp/SBND2026A_test_schung_MC/"
    "metadata_postscripts_MC_scrub_detsim_reco1_reco2_caf.sh"
)
INPUT_DATASET = "input_ds"
SHARED_INPUT = json.loads((RAW / "sam_lineage.json").read_text())["parent"]

# The stage under test is submission 3144257's: Sample3 at v10_14_02_0602.
STAGE = "mc_SBND2026A_prodgenie_corsika_proton_rockbox0p1_sbnd_test_PDS_detvar_Sample3_v10_14_02_0602"
LY094 = "mc_SBND2026A_prodgenie_corsika_proton_rockbox0p1_sbnd_PDS_detvar_1_0.94xLY_v10_14_02_04"
LY119 = "mc_SBND2026A_prodgenie_corsika_proton_rockbox0p1_sbnd_PDS_detvar_1_1.19xLY_v10_14_02_04"
AURORA = "aurora_SBND2026A_gen2_BNBLight_DevSample_prodgenie_corsika_proton_rockbox0p1_sbnd_CV_v10_14_02_03"

TIER_BY_PREFIX = {
    "scrub_detsim_reco1-": "reco1_sbnd",
    "reco2.flat.caf-": "flatcaf_sbnd",
    "caf-": "caf_sbnd",
    "larcv_mc_": "larcvreco1_sbnd",
    "hist_scrub_detsim_reco1_reco2_caf-": "histreco2_sbnd",
    "reco2-reco1-": "flatcaf_sbnd",
    "hists_reco1-": "histreco2_sbnd",
}


def launch_command(postscript_path=POSTSCRIPT):
    from urllib.parse import unquote
    details = json.loads((RAW / "submission_details_3144257.json").read_text())
    command = unquote(details["submission"]["command_executed"])
    assert REAL_POSTSCRIPT_PATH in command
    return command.replace(REAL_POSTSCRIPT_PATH, str(postscript_path))


def shared_input_tags():
    """Dataset.Tag for the 16 real children of the shared input. Which
    campaign made which file is assigned here by file_id range; only the
    0.94xLY reco1 file's tag is a real capture (sam_getMetadata_reco1.json)."""
    tags = {}
    for child in json.loads((RAW / "sam_lineage.json").read_text())["children_of_parent"]:
        name, file_id = child["file_name"], child["file_id"]
        tier = next(t for prefix, t in TIER_BY_PREFIX.items() if name.startswith(prefix))
        if name.startswith(("reco2-reco1-", "hists_reco1-")):
            campaign = AURORA
        elif file_id < 54000000:
            campaign = LY094
        elif file_id < 60000000:
            campaign = STAGE
        else:
            campaign = LY119
        tags[name] = f"{campaign}_{tier}"
    return tags


class FakeSession:
    def __init__(self, command, dataset=INPUT_DATASET):
        self.command = command
        self.dataset = dataset

    def get_launch_command(self):
        return self.command

    def get_stage_params(self):
        return {"dataset": self.dataset}


class FakeSam:
    """children: {file: [child, ...]}; tags: {file: Dataset.Tag}."""

    def __init__(self, inputs, children, tags, gone=()):
        self.inputs = inputs
        self.children = children
        self.tags = tags
        self.gone = set(gone)

    def listFiles(self, dimensions=None, defname=None):
        assert defname == INPUT_DATASET
        return iter(self.inputs)

    def getFileLineage(self, lineagetype, name):
        assert lineagetype == "children"
        if name in self.gone:
            raise FileNotFound(f"File '{name}' not found")
        return [{"file_name": c, "file_id": i} for i, c in enumerate(self.children.get(name, []))]

    def getMultipleMetadata(self, names):
        return [
            {"file_name": n, "Dataset.Tag": self.tags[n]}
            for n in names if n in self.tags and n not in self.gone
        ]


def shared_input_sam():
    tags = shared_input_tags()
    return FakeSam([SHARED_INPUT], {SHARED_INPUT: list(tags)}, tags)


def stage_files(input_name, tiers):
    """One job's outputs of the stage under test for one input."""
    return {f"{tier}-{input_name}.root": f"{STAGE}_{tier}" for tier in tiers}


ALL_TIERS = ["reco1_sbnd", "larcvreco1_sbnd", "histreco2_sbnd", "caf_sbnd", "flatcaf_sbnd"]


def names(definitions):
    return sorted(d["name"] for d in definitions)


def test_input_shared_by_four_campaigns_yields_only_this_stages_definitions(tmp_path):
    cfg = make_state_cfg(tmp_path)

    definitions = get_output_definitions(cfg, FakeSession(launch_command()), shared_input_sam())

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)
    assert all(d["depth"] == 1 for d in definitions)
    assert recorded(tmp_path) == definitions


def test_flat_list_is_inferred_again_and_overwritten(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"output_definitions": [f"{LY094}_reco1_sbnd"]}))

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), shared_input_sam())

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)
    assert recorded(tmp_path) == definitions


def test_flat_list_is_left_when_inference_fails(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"output_definitions": ["ds_reco1"]}))

    with pytest.raises(OutputDefinitionsError):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(None), NoCallSam())

    assert recorded(tmp_path) == ["ds_reco1"]


# --- dry run ---

def test_dry_run_infers_but_records_nothing(tmp_path):
    cfg = make_state_cfg(tmp_path)

    definitions = get_output_definitions(cfg, FakeSession(launch_command()), shared_input_sam(), dry_run=True)

    assert len(definitions) == 5
    assert recorded(tmp_path) is None


def test_dry_run_does_not_overwrite_a_flat_list(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"output_definitions": ["ds_reco1"]}))

    definitions = get_output_definitions(
        make_state_cfg(tmp_path), FakeSession(launch_command()), shared_input_sam(), dry_run=True,
    )

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)
    assert recorded(tmp_path) == ["ds_reco1"]


# --- which Input Dataset is sampled ---

def test_after_recovery_the_original_input_dataset_is_sampled(tmp_path):
    cfg = make_state_cfg(tmp_path, campaign_name="my_campaign")
    session = FakeSession(launch_command(), dataset=f"{INPUT_DATASET}_my_campaign_recovery_campaign")

    # FakeSam.listFiles refuses any definition but INPUT_DATASET.
    definitions = get_output_definitions(cfg, session, shared_input_sam())

    assert len(definitions) == 5


def test_small_input_dataset_is_read_in_full(tmp_path):
    most = ["histreco2_sbnd", "caf_sbnd", "flatcaf_sbnd", "larcvreco1_sbnd"]
    inputs = [f"in{i}" for i in range(25)]
    files = {name: stage_files(name, ALL_TIERS if name == "in24" else most) for name in inputs}
    tags = {f: tag for per_input in files.values() for f, tag in per_input.items()}
    sam = FakeSam(inputs, {name: list(files[name]) for name in inputs}, tags)

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert f"{STAGE}_reco1_sbnd" in names(definitions)


def test_large_input_dataset_is_sampled(tmp_path):
    most = ["histreco2_sbnd", "caf_sbnd", "flatcaf_sbnd", "larcvreco1_sbnd"]
    inputs = [f"in{i}" for i in range(201)]
    files = {name: stage_files(name, ALL_TIERS if name == "in24" else most) for name in inputs}
    tags = {f: tag for per_input in files.values() for f, tag in per_input.items()}
    sam = FakeSam(inputs, {name: list(files[name]) for name in inputs}, tags)

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert f"{STAGE}_reco1_sbnd" not in names(definitions)
    assert len(definitions) == 4


# --- scoping ---

@pytest.mark.parametrize("tag", [f"{STAGE}_reco1_sbnd_extra", f"extra_{STAGE}_reco1_sbnd"])
def test_postscript_pattern_must_match_the_whole_name(tmp_path, tag):
    tags = {**stage_files("in1", ALL_TIERS), "odd.root": tag}
    sam = FakeSam(["in1"], {"in1": list(tags)}, tags)

    with pytest.raises(OutputDefinitionsError, match="extra"):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert recorded(tmp_path) is None


SIBLING = "mc_SBND2026A_prodgenie_corsika_proton_rockbox0p1_sbnd_PDS_detvar_v10_14_02_0602"


def sibling_sam():
    """One input run by this stage (Sample3) and by its sibling PDS_detvar,
    same version."""
    tags = {**stage_files("in1", ALL_TIERS), "sibling_reco1.root": f"{SIBLING}_reco1_sbnd"}
    return FakeSam(["in1"], {"in1": list(tags)}, tags)


def test_fallback_does_not_take_a_longer_named_siblings_definitions(tmp_path):
    command = launch_command(tmp_path / "metadata_postscripts_gone.sh").replace("test_PDS_detvar_Sample3", "PDS_detvar")

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(command), sibling_sam())

    assert names(definitions) == [f"{SIBLING}_reco1_sbnd"]


def test_fallback_does_not_take_a_shorter_named_siblings_definitions(tmp_path):
    command = launch_command(tmp_path / "metadata_postscripts_gone.sh")

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(command), sibling_sam())

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)


def test_postscript_pattern_with_no_file_under_it_is_fine(tmp_path):
    tags = stage_files("in1", ["histreco2_sbnd", "caf_sbnd", "flatcaf_sbnd"])
    sam = FakeSam(["in1"], {"in1": list(tags)}, tags)

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert names(definitions) == sorted(f"{STAGE}_{t}" for t in ["histreco2_sbnd", "caf_sbnd", "flatcaf_sbnd"])


def test_disagreement_records_nothing_and_raises(tmp_path, caplog):
    tags = stage_files("in1", ALL_TIERS + ["calib_sbnd"])
    sam = FakeSam(["in1"], {"in1": list(tags)}, tags)

    with pytest.raises(OutputDefinitionsError, match="calib_sbnd"):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert recorded(tmp_path) is None
    # Both lists are logged: the postscript's patterns and the lineage names.
    assert f"{STAGE}_larcvreco1_sbnd" in caplog.text
    assert f"{STAGE}_calib_sbnd" in caplog.text


def test_real_twin_of_a_test_campaign_is_not_a_disagreement(tmp_path):
    # Same inputs, same version, production name without the test_ prefix.
    twin = STAGE.replace("_test_PDS_detvar_Sample3_", "_PDS_detvar_Sample3_")
    tags = {**stage_files("in1", ALL_TIERS), "twin_reco1.root": f"{twin}_reco1_sbnd"}
    sam = FakeSam(["in1"], {"in1": list(tags)}, tags)

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)


@pytest.mark.parametrize("prefix", ["test_", "test2_"])
def test_a_test_launchs_outputs_under_the_same_inputs_are_ignored_and_logged_once(tmp_path, caplog, prefix):
    caplog.set_level(logging.INFO)
    tags = stage_files("in1", ALL_TIERS)
    for tier in ALL_TIERS:
        tags[f"test_{tier}-in1.root"] = f"{prefix}{STAGE}_{tier}"
    sam = FakeSam(["in1"], {"in1": list(tags)}, tags)
    cfg = make_state_cfg(tmp_path)

    definitions = get_output_definitions(cfg, FakeSession(launch_command()), sam)

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)
    assert recorded(tmp_path) == definitions
    ignored = [r for r in caplog.records if "ignoring" in r.getMessage() and r.levelno == logging.INFO]
    assert len(ignored) == 1


def test_a_test_launch_tag_does_not_hide_a_real_disagreement(tmp_path):
    tags = stage_files("in1", ALL_TIERS + ["calib_sbnd"])
    tags["test_x-in1.root"] = f"test_{STAGE}_caf_sbnd"
    sam = FakeSam(["in1"], {"in1": list(tags)}, tags)

    with pytest.raises(OutputDefinitionsError, match="calib_sbnd"):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)


def test_missing_postscript_falls_back_to_scoped_lineage_and_logs_it(tmp_path, caplog):
    cfg = make_state_cfg(tmp_path)
    session = FakeSession(launch_command(tmp_path / "metadata_postscripts_gone.sh"))

    with caplog.at_level("WARNING"):
        definitions = get_output_definitions(cfg, session, shared_input_sam())

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)
    assert recorded(tmp_path) == definitions
    assert "fallback" in caplog.text


def test_no_input_with_children_raises_and_leaves_state_untouched(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"last_split": 3}))
    sam = FakeSam(["in1", "in2"], {}, {})

    with pytest.raises(OutputDefinitionsError):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert json.loads((tmp_path / "state.json").read_text()) == {"last_split": 3}


def test_only_other_campaigns_outputs_raises(tmp_path):
    tags = {name: tag for name, tag in shared_input_tags().items() if not tag.startswith(STAGE)}
    sam = FakeSam([SHARED_INPUT], {SHARED_INPUT: list(tags)}, tags)

    with pytest.raises(OutputDefinitionsError):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert recorded(tmp_path) is None


def test_no_launch_command_raises_without_touching_sam(tmp_path):
    with pytest.raises(OutputDefinitionsError, match="launch command"):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(None), NoCallSam())

    assert recorded(tmp_path) is None


def test_tier_missing_from_first_input_is_found_from_another(tmp_path):
    first = stage_files("in1", ["histreco2_sbnd", "caf_sbnd", "flatcaf_sbnd"])
    second = stage_files("in3", ALL_TIERS)
    sam = FakeSam(["in1", "in2", "in3"], {"in1": list(first), "in3": list(second)}, {**first, **second})

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert f"{STAGE}_reco1_sbnd" in names(definitions)
    assert len(definitions) == 5


def test_depth_two_descendants_are_recorded_with_depth_two(tmp_path):
    children = {"in1": ["reco1.root"], "reco1.root": ["caf.root"]}
    tags = {"reco1.root": f"{STAGE}_reco1_sbnd", "caf.root": f"{STAGE}_caf_sbnd"}

    definitions = get_output_definitions(
        make_state_cfg(tmp_path), FakeSession(launch_command()), FakeSam(["in1"], children, tags)
    )

    assert definitions == [
        {"name": f"{STAGE}_reco1_sbnd", "depth": 1},
        {"name": f"{STAGE}_caf_sbnd", "depth": 2},
    ]


# --- retired files: docs/raw/sam_retired_file_2026-10-13.txt ---

def test_retired_descendant_is_skipped(tmp_path):
    children = {"in1": ["reco1_old.root", "reco1.root"], "reco1.root": ["caf.root"], "reco1_old.root": ["caf_old.root"]}
    tags = {
        "reco1.root": f"{STAGE}_reco1_sbnd", "caf.root": f"{STAGE}_caf_sbnd",
        "reco1_old.root": f"{STAGE}_larcvreco1_sbnd", "caf_old.root": f"{STAGE}_histreco2_sbnd",
    }
    sam = FakeSam(["in1"], children, tags, gone={"reco1_old.root"})

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert definitions == [
        {"name": f"{STAGE}_reco1_sbnd", "depth": 1},
        {"name": f"{STAGE}_caf_sbnd", "depth": 2},
    ]


def test_input_that_is_gone_is_skipped(tmp_path):
    files = stage_files("in2", ALL_TIERS)
    sam = FakeSam(["in1", "in2"], {"in1": ["never_read.root"], "in2": list(files)}, files, gone={"in1"})

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert len(definitions) == 5


def test_other_sam_error_in_the_lineage_walk_propagates(tmp_path):
    sam = FakeSam(["in1"], {"in1": ["reco1.root"]}, {"reco1.root": f"{STAGE}_reco1_sbnd"})
    lineage = sam.getFileLineage

    def broken(lineagetype, name):
        if name == "reco1.root":
            raise RuntimeError("503 Service Unavailable")
        return lineage(lineagetype, name)

    sam.getFileLineage = broken

    with pytest.raises(RuntimeError, match="503"):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)
    assert recorded(tmp_path) is None


# sbnutil's metadata_postscripts.sh style: a variable the launch command
# never exports, and a commented-out block of reassignments.
STREAM_POSTSCRIPT = """
export MT_DTAG=${MT_PRODUCTIONTYPE}_${MT_PRODUCTIONLABEL}_${MT_PRODUCTIONNAME}_${MT_SBND_STREAM_NAME}_${SBNDCODE_VERSION}_${MT_PROJECTSTAGE}_sbnd
export FLAT_MT_DTAG="${MT_PRODUCTIONTYPE}_${MT_PRODUCTIONLABEL}_${MT_PRODUCTIONNAME}_${MT_SBND_STREAM_NAME}_${SBNDCODE_VERSION}_flat${MT_PROJECTSTAGE}_sbnd"
#    MT_DTAG=test_${MT_DTAG}
"""


def test_unknown_postscript_variable_is_a_wildcard(tmp_path):
    postscript = tmp_path / "metadata_postscripts.sh"
    postscript.write_text(STREAM_POSTSCRIPT)
    base = "mc_SBND2026A_test_PDS_detvar_Sample3_{}_v10_14_02_0602_{}scrub_detsim_reco1_reco2_caf_sbnd"
    tags = {
        "a.root": base.format("bnblight", ""),
        "b.root": base.format("offbeam", "flat"),
        "c.root": f"{LY094}_reco1_sbnd",
    }
    sam = FakeSam(["in1"], {"in1": list(tags)}, tags)

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command(postscript)), sam)

    assert names(definitions) == sorted([tags["a.root"], tags["b.root"]])


def test_commented_out_assignment_is_not_a_pattern(tmp_path):
    postscript = tmp_path / "metadata_postscripts.sh"
    postscript.write_text(STREAM_POSTSCRIPT)
    tag = "test_mc_SBND2026A_test_PDS_detvar_Sample3_bnb_v10_14_02_0602_scrub_detsim_reco1_reco2_caf_sbnd"
    sam = FakeSam(["in1"], {"in1": ["a.root"]}, {"a.root": tag})

    with pytest.raises(OutputDefinitionsError):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command(postscript)), sam)
