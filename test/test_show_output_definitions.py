import os

import pytest

import state
import show_output_definitions
import test_output_definitions as tod
from output_definitions import describe_lineage_tags, get_output_definitions

TEST_TAG = f"test1_{tod.STAGE}_reco1_sbnd"


class DatedSam(tod.FakeSam):
    """FakeSam that also has create_date and countFiles; any other call is
    an AttributeError, so a write cannot go unnoticed."""

    def __init__(self, inputs, children, tags, dates, counts=None):
        super().__init__(inputs, children, tags)
        self.dates = dates
        self.counts = counts or {}
        self.count_calls = []

    def getMultipleMetadata(self, names):
        rows = super().getMultipleMetadata(names)
        for row in rows:
            row["create_date"] = self.dates.get(row["file_name"], "2026-01-01T00:00:00+00:00")
        return rows

    def countFiles(self, defname=None):
        self.count_calls.append(defname)
        return self.counts.get(defname, 0)


def dated_sam(counts=None):
    tags = tod.shared_input_tags()
    children = list(tags)
    tags["test_file.root"] = TEST_TAG
    children.append("test_file.root")
    dates = {name: "2026-02-01T00:00:00+00:00" for name in tags}
    # Two files of one tag: the newest date is reported.
    first_stage_file = next(n for n, t in tags.items() if t.startswith(tod.STAGE))
    dates[first_stage_file] = "2026-11-01T12:00:00+00:00"
    dates["test_file.root"] = "2026-11-09T00:00:00+00:00"
    return DatedSam([tod.SHARED_INPUT], {tod.SHARED_INPUT: children}, tags, dates, counts), first_stage_file


def by_tag(rows):
    return {r["tag"]: r for r in rows}


def test_describe_lineage_tags_says_accepted_rejected_or_test(tmp_path):
    sam, _ = dated_sam()
    session = tod.FakeSession(tod.launch_command())

    rows = by_tag(describe_lineage_tags(tod.make_cfg(), session, sam))

    assert rows[f"{tod.STAGE}_reco1_sbnd"]["status"] == "accepted"
    assert rows[f"{tod.LY094}_reco1_sbnd"]["status"] == "rejected"
    assert rows[f"{tod.AURORA}_flatcaf_sbnd"]["status"] == "rejected"
    assert rows[TEST_TAG]["status"] == "test"
    assert rows[TEST_TAG]["depth"] == 1


def test_describe_lineage_tags_reports_the_newest_create_date_of_each_tag():
    sam, first_stage_file = dated_sam()
    tag = sam.tags[first_stage_file]

    rows = by_tag(describe_lineage_tags(tod.make_cfg(), tod.FakeSession(tod.launch_command()), sam))

    assert rows[tag]["newest_create_date"] == "2026-11-01T12:00:00+00:00"
    assert rows[TEST_TAG]["newest_create_date"] == "2026-11-09T00:00:00+00:00"


def test_accepted_tags_are_what_get_output_definitions_returns():
    sam, _ = dated_sam()
    session = tod.FakeSession(tod.launch_command())
    cfg = tod.make_cfg()

    rows = describe_lineage_tags(cfg, session, sam)
    definitions = get_output_definitions(cfg, session, sam, dry_run=True)

    assert sorted(r["tag"] for r in rows if r["status"] == "accepted") == sorted(d["name"] for d in definitions)


def run_tool(argv, sam, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    code = show_output_definitions.main(argv, session=tod.FakeSession(tod.launch_command()), sam=sam)
    return code, capsys.readouterr().out


def test_tool_prints_name_depth_tier_and_file_count(capsys, monkeypatch, tmp_path):
    reco1 = f"{tod.STAGE}_reco1_sbnd"
    sam, _ = dated_sam(counts={reco1: 321})

    code, out = run_tool(["--campaign", "c", "--stage", "s"], sam, capsys, monkeypatch, tmp_path)

    assert code == 0
    line = next(line for line in out.splitlines() if reco1 in line)
    assert line.split() == [reco1, "1", "reco1", "321"]
    assert any(line.split()[2] == "flatcaf" for line in out.splitlines() if tod.STAGE in line)
    assert TEST_TAG not in out
    assert tod.LY094 not in out


def test_tool_all_tags_lists_every_tag_with_date_and_status(capsys, monkeypatch, tmp_path):
    sam, _ = dated_sam()

    code, out = run_tool(["--campaign", "c", "--stage", "s", "--all-tags"], sam, capsys, monkeypatch, tmp_path)

    assert code == 0
    test_line = next(line for line in out.splitlines() if TEST_TAG in line)
    assert "2026-11-09" in test_line and "test" in test_line.split()
    other = next(line for line in out.splitlines() if f"{tod.LY094}_reco1_sbnd" in line)
    assert "rejected" in other.split()


def test_tool_writes_nothing(capsys, monkeypatch, tmp_path):
    sam, _ = dated_sam()
    written = []
    monkeypatch.setattr(state, "write", lambda *a, **k: written.append(a))

    run_tool(["--campaign", "c", "--stage", "s", "--all-tags"], sam, capsys, monkeypatch, tmp_path)

    assert written == []
    assert os.listdir(tmp_path) == []


def test_the_cfg_has_no_state_file_so_state_write_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = show_output_definitions.build_cfg("c", "s", "sbnd")

    state.write(cfg, "output_definitions", [{"name": "x", "depth": 1}])

    assert "state_file" not in cfg
    assert cfg["role"] == "production"
    assert os.listdir(tmp_path) == []


def test_tool_reports_an_inference_error_with_a_nonzero_exit(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    code = show_output_definitions.main(
        ["--campaign", "c", "--stage", "s"], session=tod.FakeSession(None), sam=tod.NoCallSam(),
    )

    assert code == 1
    assert "no Submission with a launch command" in capsys.readouterr().err
