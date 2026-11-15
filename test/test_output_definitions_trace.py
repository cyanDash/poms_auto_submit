import logging
import re

import pytest

import test_output_definitions as tod
from output_definitions import OutputDefinitionsError, get_output_definitions, trace_output_definitions

PREFIX = "output definitions: step "


class SourceSession(tod.FakeSession):
    def __init__(self, command, submission_id=3144257, is_test=False, windows=1, **kwargs):
        super().__init__(command, **kwargs)
        self.source = {"command": command, "submission_id": submission_id, "is_test": is_test, "windows": windows}

    def get_launch_source(self):
        return self.source


def logged_steps(caplog):
    """{step number: message after the prefix} from the INFO trace lines."""
    steps = {}
    for record in caplog.records:
        message = record.getMessage()
        if record.levelno == logging.INFO and message.startswith(PREFIX):
            number = int(re.match(r"(\d+)", message[len(PREFIX):]).group(1))
            steps[number] = message[len(PREFIX):]
    return steps


def run(tmp_path, caplog, session, sam, expect_error=False):
    caplog.set_level(logging.INFO)
    cfg = tod.make_state_cfg(tmp_path)
    if expect_error:
        with pytest.raises(OutputDefinitionsError):
            get_output_definitions(cfg, session, sam)
    else:
        get_output_definitions(cfg, session, sam)
    return logged_steps(caplog)


def test_the_trace_has_nine_steps_in_order_each_with_the_prefix(tmp_path, caplog):
    steps = run(tmp_path, caplog, SourceSession(tod.launch_command()), tod.shared_input_sam())

    assert sorted(steps) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    names = [steps[n].split(":")[0] for n in sorted(steps)]
    assert names == [
        "1 launch command", "2 input dataset", "3 lineage tags", "4 test launch tags",
        "5 postscript patterns", "6 candidates", "7 verification", "8 classification", "9 result",
    ]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith(PREFIX)]
    assert [int(re.match(r"(\d+)", line[len(PREFIX):]).group(1)) for line in lines] == [1, 2, 3, 4, 5, 6, 7, 8, 9]


def test_step_one_says_how_many_poms_windows_were_read(tmp_path, caplog):
    steps = run(tmp_path, caplog, SourceSession(tod.launch_command(), windows=3), tod.shared_input_sam())

    assert "3 POMS window(s) read" in steps[1]


def test_input_shared_with_other_campaigns_is_traced_step_by_step(tmp_path, caplog):
    steps = run(tmp_path, caplog, SourceSession(tod.launch_command()), tod.shared_input_sam())

    assert "Submission 3144257" in steps[1] and "not a Test Launch" in steps[1]
    assert "test_PDS_detvar_Sample3" in steps[1] and "v10_14_02_0602" in steps[1]
    assert tod.POSTSCRIPT.name in steps[1] and "readable" in steps[1]
    assert "input_ds" in steps[2] and "1 input" in steps[2]
    # Every lineage tag is listed with its depth, the other campaigns' too.
    assert f"{tod.LY094}_reco1_sbnd (depth 1" in steps[3]
    assert f"{tod.AURORA}_flatcaf_sbnd (depth 1" in steps[3]
    assert f"{tod.STAGE}_caf_sbnd (depth 1" in steps[3]
    # The stage's tags are kept, the others dropped as another campaign's.
    keeps, drops = steps[8].split(" | drops: ")
    assert f"{tod.STAGE}_caf_sbnd (depth 1)" in keeps
    assert f"{tod.LY094}_reco1_sbnd" not in keeps
    assert f"{tod.LY094}_reco1_sbnd (depth 1): another campaign's" in drops
    assert f"{tod.AURORA}_flatcaf_sbnd (depth 1): another campaign's" in drops
    assert f"{tod.STAGE}_caf_sbnd (depth 1)" in steps[9] and "recorded" in steps[9]


def test_a_test_launch_tag_is_dropped_at_step_4_with_its_reason(tmp_path, caplog):
    tags = tod.stage_files("in1", tod.ALL_TIERS)
    tags["t.root"] = f"test2_{tod.STAGE}_reco1_sbnd"
    sam = tod.FakeSam(["in1"], {"in1": list(tags)}, tags)

    steps = run(tmp_path, caplog, SourceSession(tod.launch_command()), sam)

    keeps, drops = steps[4].split(" | drops: ")
    assert f"test2_{tod.STAGE}_reco1_sbnd (depth 1): Test Launch tag" in drops
    assert f"test2_{tod.STAGE}_reco1_sbnd" not in keeps
    assert f"{tod.STAGE}_reco1_sbnd (depth 1)" in keeps
    assert f"test2_{tod.STAGE}_reco1_sbnd" not in steps[8]


def test_a_disagreement_is_traced_and_nothing_is_recorded(tmp_path, caplog):
    tags = tod.stage_files("in1", tod.ALL_TIERS + ["calib_sbnd"])
    sam = tod.FakeSam(["in1"], {"in1": list(tags)}, tags)

    steps = run(tmp_path, caplog, SourceSession(tod.launch_command()), sam, expect_error=True)

    assert f"{tod.STAGE}_calib_sbnd (depth 1): disagreement" in steps[8]
    assert "carries the production name and version" in steps[8]
    assert "nothing recorded" in steps[9] and "calib_sbnd" in steps[9]


def test_without_a_postscript_the_fallback_scope_and_its_tags_are_traced(tmp_path, caplog):
    session = SourceSession(tod.launch_command(tmp_path / "metadata_postscripts_gone.sh"))

    steps = run(tmp_path, caplog, session, tod.shared_input_sam())

    assert "not readable" in steps[1]
    assert "not readable" in steps[5]
    assert "PDS_detvar_Sample3_v10_14_02_0602" in steps[8]
    keeps, drops = steps[8].split(" | drops: ")
    assert f"{tod.STAGE}_caf_sbnd (depth 1)" in keeps
    assert f"{tod.LY094}_reco1_sbnd (depth 1): does not carry" in drops


def test_step_1_says_where_the_version_came_from(tmp_path, caplog):
    for sub in "bc":
        (tmp_path / sub).mkdir()
    steps = run(tmp_path, caplog, SourceSession(tod.launch_command()), tod.shared_input_sam())
    assert "version v10_14_02_0602 (from --mdappversion)" in steps[1]

    caplog.clear()
    steps = run(tmp_path / "b", caplog, SourceSession(tod.nusyst_command()), tod.nusyst_sam())
    assert "version v1_01_00 (from MT_PROJECTVERSION)" in steps[1]

    caplog.clear()
    command = tod.nusyst_command().replace("export MT_PROJECTVERSION=v1_01_00 && ", "").replace(
        "--setup-unquote sbnnusyst v1_01_00", "--setup-unquote sbndcode v1_01_00"
    )
    steps = run(tmp_path / "c", caplog, SourceSession(command), tod.nusyst_sam())
    assert "version v1_01_00 (from the sbndcode/icaruscode setup)" in steps[1]


def test_the_fallback_scope_names_the_production_name_and_the_sample(tmp_path, caplog):
    steps = run(tmp_path, caplog, SourceSession(tod.nusyst_command()), tod.nusyst_sam())

    assert "Ar23plus_caf_Repro_IntrinsicNuE_lynnt_v1_01_00" in steps[8]
    assert "IntrinsicNuE_CV_v1_01_00" in steps[8]


def test_a_fallback_launch_command_and_a_recovery_dataset_are_said_so(tmp_path, caplog):
    recovery = f"{tod.INPUT_DATASET}_{tod.make_cfg()['campaign_name']}_recovery_campaign"
    session = SourceSession(tod.launch_command(), is_test=True, dataset=recovery)

    steps = run(tmp_path, caplog, session, tod.shared_input_sam())

    assert "newest of any kind" in steps[1]
    assert recovery in steps[2] and tod.INPUT_DATASET in steps[2] and "original" in steps[2]


def test_trace_output_definitions_returns_the_steps_without_raising(tmp_path):
    tags = tod.stage_files("in1", tod.ALL_TIERS + ["calib_sbnd"])
    sam = tod.FakeSam(["in1"], {"in1": list(tags)}, tags)

    trace = trace_output_definitions(tod.make_cfg(), SourceSession(tod.launch_command()), sam)

    assert [s["step"] for s in trace["steps"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert "calib_sbnd" in trace["error"]
    assert trace["definitions"] == []
    assert {r["tag"] for r in trace["rows"]} >= {f"{tod.STAGE}_calib_sbnd"}


def test_no_launch_command_stops_the_trace_at_step_1_and_the_result(tmp_path, caplog):
    steps = run(tmp_path, caplog, SourceSession(None), tod.NoCallSam(), expect_error=True)

    assert sorted(steps) == [1, 9]
    assert "no Submission with a launch command" in steps[1]
