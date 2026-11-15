import json
import logging
from pathlib import Path

import pytest

import state
from helpers import DimensionError, FileNotFound, make_cfg
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
    """children: {file: [child, ...]}; tags: {file: Dataset.Tag}; a file's
    parents are the files listing it as a child, in input order. `meta` is
    extra metadata per file; `definitions` the names listDefinitions gives."""

    def __init__(self, inputs, children, tags, gone=(), meta=None, definitions=()):
        self.inputs = inputs
        self.children = children
        self.tags = tags
        self.gone = set(gone)
        self.meta = meta or {}
        self.definitions = list(definitions)
        self.calls = []

    def listDefinitions(self, **criteria):
        self.calls.append(("listDefinitions", criteria))
        return list(self.definitions)

    def countFiles(self, dimensions=None, defname=None):
        self.calls.append(("countFiles", dimensions))
        return len(self._tagged(dimensions))

    def _tagged(self, dimensions):
        tag = dimensions.split()[1]
        return [f for f, t in self.tags.items() if t == tag and f not in self.gone]

    def listFiles(self, dimensions=None, defname=None):
        self.calls.append(("listFiles", dimensions or defname))
        if defname is not None:
            assert defname == INPUT_DATASET
            return iter(self.inputs)
        if dimensions.startswith("defname: ") and " with limit " in dimensions:
            raise DimensionError("No existing dataset definition")
        if dimensions.startswith("dataset.tag "):
            limit = int(dimensions.split(" with limit ")[1])
            return iter(self._tagged(dimensions)[:limit])
        head, names = dimensions.split(" and file_name ")
        assert head == f"defname: {INPUT_DATASET}"
        return iter([n for n in names.split(",") if n in self.inputs])

    def getFileLineage(self, lineagetype, name):
        if name in self.gone:
            raise FileNotFound(f"File '{name}' not found")
        if lineagetype == "parents":
            return [{"file_name": p} for p, kids in self.children.items() if name in kids]
        assert lineagetype == "children"
        return [{"file_name": c, "file_id": i} for i, c in enumerate(self.children.get(name, []))]

    def getMultipleMetadata(self, names):
        return [
            {"file_name": n, "Dataset.Tag": self.tags[n], **self.meta.get(n, {})}
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


def test_large_input_dataset_is_sampled_but_a_name_the_scan_missed_is_still_found(tmp_path):
    most = ["histreco2_sbnd", "caf_sbnd", "flatcaf_sbnd", "larcvreco1_sbnd"]
    inputs = [f"in{i}" for i in range(201)]
    files = {name: stage_files(name, ALL_TIERS if name == "in24" else most) for name in inputs}
    tags = {f: tag for per_input in files.values() for f, tag in per_input.items()}
    sam = FakeSam(inputs, {name: list(files[name]) for name in inputs}, tags)

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert len(definitions) == 5


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
    ignored = [
        r for r in caplog.records
        if "step 4 test launch tags" in r.getMessage() and r.levelno == logging.INFO
    ]
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


# --- no --mdappversion: the sbnnusyst reprocess (submission 3179008) ---

NUSYST_TAG_NEW = "Reprocess_mc_SBND2026A_AR23plus_knobs_IntrinsicNuE_CV_v1_01_00_flatcaf_sbnd"
NUSYST_TAG_OLD = "mc_SBND2026A_AR23plus_knobs_IntrinsicNuE_CV_v1_00_01_flatcaf_sbnd"
NUSYST_POSTSCRIPT = (
    "/exp/sbnd/app/users/sdas1/production_helper_scripts/ar23plus_cfg/scripts/metadata_postscripts_sbnnusyst.sh"
)


def nusyst_command(postscript="/nonexistent/metadata_postscripts_gone.sh"):
    from urllib.parse import unquote
    details = json.loads((RAW / "submission_details_3179008.json").read_text())
    command = unquote(details["submission"]["command_executed"])
    assert NUSYST_POSTSCRIPT in command
    return command.replace(NUSYST_POSTSCRIPT, str(postscript))


def nusyst_sam(tags=(NUSYST_TAG_NEW, NUSYST_TAG_OLD)):
    files = {f"out{i}.root": t for i, t in enumerate(tags)}
    return FakeSam(["in1"], {"in1": list(files)}, files)


def test_fallback_scopes_by_sample_and_project_version_when_there_is_no_mdappversion(tmp_path):
    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(nusyst_command()), nusyst_sam())

    assert names(definitions) == [NUSYST_TAG_NEW]


def test_fallback_takes_the_version_from_the_setup_when_nothing_else_names_it(tmp_path):
    command = nusyst_command().replace("export MT_PROJECTVERSION=v1_01_00 && ", "").replace(
        "--setup-unquote sbnnusyst v1_01_00", "--setup-unquote sbndcode v1_01_00"
    )

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(command), nusyst_sam())

    assert names(definitions) == [NUSYST_TAG_NEW]


def test_fallback_with_no_version_anywhere_raises(tmp_path):
    command = nusyst_command().replace("export MT_PROJECTVERSION=v1_01_00 && ", "")

    with pytest.raises(OutputDefinitionsError):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(command), nusyst_sam())


def test_fallback_does_not_take_another_samples_tag_of_the_same_version(tmp_path):
    other = NUSYST_TAG_NEW.replace("IntrinsicNuE_CV", "Other_CV")

    definitions = get_output_definitions(
        make_state_cfg(tmp_path), FakeSession(nusyst_command()), nusyst_sam((NUSYST_TAG_NEW, other))
    )

    assert names(definitions) == [NUSYST_TAG_NEW]


def test_disagreement_check_sees_a_sample_and_version_tag_that_matches_no_pattern(tmp_path):
    postscript = tmp_path / "metadata_postscripts_x.sh"
    postscript.write_text("DTAG=Reprocess_${MT_PRODUCTIONTYPE}_${MT_SAMPLE}_${MT_PROJECTVERSION}_caf_sbnd\n")

    with pytest.raises(OutputDefinitionsError, match="flatcaf_sbnd"):
        get_output_definitions(
            make_state_cfg(tmp_path), FakeSession(nusyst_command(postscript)), nusyst_sam()
        )


# --- name first, then verify ---

def beamlight_sam(n_inputs=1000, own_from=900, tiers=ALL_TIERS, **kw):
    """The first inputs only have children of another campaign; the stage's
    own outputs are under later inputs."""
    inputs = [f"in{i}" for i in range(n_inputs)]
    children, tags = {}, {}
    for i in range(30):
        f = f"old{i}_reco1.root"
        children[f"in{i}"] = [f]
        tags[f] = f"{LY094}_reco1_sbnd"
    for i in range(own_from, own_from + 3):
        files = stage_files(f"in{i}", tiers)
        children[f"in{i}"] = list(files)
        tags.update(files)
    return FakeSam(inputs, children, tags, **kw)


def trace_of(sam, command=None):
    from output_definitions import trace_output_definitions
    return trace_output_definitions(make_cfg(), FakeSession(command or launch_command()), sam)


def step_named(trace, name):
    return next(s for s in trace["steps"] if s["name"] == name)


def test_beamlight_names_from_postscript_are_found_though_the_scan_sees_only_another_campaign(tmp_path):
    sam = beamlight_sam()

    definitions = get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert names(definitions) == sorted(f"{STAGE}_{tier}" for tier in ALL_TIERS)
    assert all(d["depth"] == 1 for d in definitions)


def test_verification_checks_parents_against_the_inputs_already_read(tmp_path):
    sam = beamlight_sam()

    get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam)

    assert not [c for c in sam.calls if c[0] == "listFiles" and " and file_name " in (c[1] or "")]
    assert sum(1 for c in sam.calls if c == ("listFiles", INPUT_DATASET)) == 1


def test_every_kth_input_is_probed_when_the_dataset_is_large():
    probed = []

    class Spy(FakeSam):
        def getFileLineage(self, lineagetype, name):
            if lineagetype == "children":
                probed.append(name)
            return super().getFileLineage(lineagetype, name)

    trace_of(Spy([f"in{i}" for i in range(1000)], {}, {}))

    assert probed[:3] == ["in0", "in5", "in10"]
    assert len(probed) == 200


def test_a_candidate_with_no_files_in_sam_is_listed_with_the_reason():
    sam = beamlight_sam()
    for f in [f for f, t in sam.tags.items() if t == f"{STAGE}_caf_sbnd"]:
        del sam.tags[f]

    trace = trace_of(sam)

    drops = step_named(trace, "verification")["drops"]
    assert any(e["tag"] == f"{STAGE}_caf_sbnd" and "no files in SAM" in e["reason"] for e in drops)
    assert len(trace["definitions"]) == 4


def test_a_candidate_whose_files_are_not_descendants_of_the_input_dataset_is_dropped():
    sam = beamlight_sam()
    sam.tags["orphan.root"] = f"{STAGE}_caf_sbnd"
    sam.children["other_input"] = ["orphan.root"]

    trace = trace_of(sam)

    drops = step_named(trace, "verification")["drops"]
    assert any(
        e["tag"] == f"{STAGE}_caf_sbnd" and "not descendants of this Input Dataset" in e["reason"] for e in drops
    )
    assert f"{STAGE}_caf_sbnd" not in names(trace["definitions"])


def test_a_chain_up_to_the_input_gives_the_depth():
    children = {"in1": ["mid.root"], "mid.root": ["out.root"]}
    tags = {"mid.root": f"{STAGE}_reco1_sbnd", "out.root": f"{STAGE}_caf_sbnd"}

    depths = {d["name"]: d["depth"] for d in trace_of(FakeSam(["in1"], children, tags))["definitions"]}

    assert depths == {f"{STAGE}_reco1_sbnd": 1, f"{STAGE}_caf_sbnd": 2}


def test_metadata_that_contradicts_the_launch_command_drops_the_candidate():
    sam = beamlight_sam()
    for f, t in sam.tags.items():
        if t == f"{STAGE}_caf_sbnd":
            sam.meta[f] = {"production.name": "some_other_production"}

    trace = trace_of(sam)

    drops = step_named(trace, "verification")["drops"]
    assert any(e["tag"] == f"{STAGE}_caf_sbnd" and "production.name" in e["reason"] for e in drops)
    assert f"{STAGE}_caf_sbnd" not in names(trace["definitions"])


def wildcard_command(tmp_path):
    postscript = tmp_path / "metadata_postscripts_wild.sh"
    postscript.write_text('DTAG_CAF="${NOPE}_caf_sbnd"\n')
    return launch_command(postscript)


def test_wildcard_patterns_ask_sam_for_definitions_with_percent_and_verify_them(tmp_path):
    sam = beamlight_sam(tiers=["caf_sbnd"], definitions=[f"{STAGE}_caf_sbnd", "unrelated_name"])

    trace = trace_of(sam, wildcard_command(tmp_path))

    assert ("listDefinitions", {"defname": "%_caf_sbnd"}) in sam.calls
    assert names(trace["definitions"]) == [f"{STAGE}_caf_sbnd"]


def test_trace_steps_are_named_and_numbered_for_candidates_and_verification():
    trace = trace_of(beamlight_sam())

    assert [(s["step"], s["name"]) for s in trace["steps"]] == [
        (1, "launch command"), (2, "input dataset"), (3, "lineage tags"), (4, "test launch tags"),
        (5, "postscript patterns"), (6, "candidates"), (7, "verification"), (8, "classification"),
        (9, "result"),
    ]


def test_a_tag_that_carries_the_stage_identity_but_matches_no_pattern_still_raises(tmp_path):
    sam = beamlight_sam()
    sam.tags["odd.root"] = f"{STAGE}_calib_sbnd"
    sam.children["in0"] = sam.children["in0"] + ["odd.root"]

    with pytest.raises(OutputDefinitionsError, match="calib_sbnd"):
        get_output_definitions(make_state_cfg(tmp_path), FakeSession(launch_command()), sam, dry_run=True)
