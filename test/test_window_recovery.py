import pytest

import state
import window_recovery
from helpers import make_cfg

WINDOW = "run3_raw_data_ongoing_b78c_time_1794722400__1794733200"
COMMAND = f"fife_launch -c x.cfg -Oglobal.sam_dataset={WINDOW} -Osubmit.memory=10GB"
DEFINITIONS = [{"name": "out_reco1_sbnd", "depth": 1}, {"name": "out_caf_sbnd", "depth": 1}, {"name": "deep_sbnd", "depth": 2}]
SAFE_STAGE = {
    "dataset": "run3_raw_data_ongoing",
    "test_split_type": "",
    "param_overrides": [["-Oglobal.basedir=", "/pnfs/x"], ["-Osubmit.memory=", "10GB"], ["-Osubmit.subgroup=", "pro"]],
    "test_param_overrides": [["-Osubmit.subgroup=", "test"], ["-Oglobal.basedir=", "/pnfs/x"]],
}


DEFAULT = object()


class FakeSession:
    def __init__(self, events, source=DEFAULT, stage=None, submit_result="9001", fail_launch=False):
        self.events = events
        self.campaign_stage_id = 27154
        self._source = source if source is not DEFAULT else {"command": COMMAND, "submission_id": 3181441, "is_test": False}
        self._stage = stage or SAFE_STAGE
        self._submit_result = submit_result
        self._fail_launch = fail_launch

    def get_launch_source(self):
        return self._source

    def get_stage_params(self):
        return self._stage

    def update_test_stage_params(self, updates):
        self.events.append(("test_params", updates))

    def set_input_dataset(self, name):
        self.events.append(("dataset", name))

    def submit_next_slice(self):
        if self._fail_launch:
            raise RuntimeError("launch_jobs failed")
        self.events.append(("test_launch",))
        return self._submit_result


class FakeSam:
    def __init__(self):
        self.queries = []

    def countFiles(self, dimensions):
        self.queries.append(dimensions)
        return 7


def run_case(monkeypatch, tmp_path, script_result=(0.8, 1.0, "rec_ds"), dry_run=False, recorded=None, **session_kw):
    events, scripts = [], []
    session = FakeSession(events, **session_kw)
    cfg = make_cfg(cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"))
    if recorded is not None:
        state.write(cfg, window_recovery.SECTION, recorded)
    monkeypatch.setattr(window_recovery.output_definitions, "get_output_definitions", lambda *a, **k: DEFINITIONS)

    def fake_script(dataset, campaign, prestage_log, names, ratio_threshold=None):
        scripts.append((dataset, names, ratio_threshold))
        return script_result

    monkeypatch.setattr(window_recovery.recovery, "run_recovery_script", fake_script)
    sam = FakeSam()
    outcome = window_recovery.recover_window(cfg, session, session, sam, dry_run=dry_run)
    return outcome, cfg, events, scripts, sam


def test_window_dataset_is_read_from_the_launch_command():
    assert window_recovery.window_dataset(COMMAND) == WINDOW
    assert window_recovery.window_dataset(f"jobsub_submit -G sbnd --dataset_definition={WINDOW} --add_to_dataset") == WINDOW
    assert window_recovery.window_dataset("jobsub_submit -N 3") is None


def test_recovers_the_window_with_depth_one_outputs_at_threshold_one(monkeypatch, tmp_path):
    outcome, cfg, events, scripts, _ = run_case(monkeypatch, tmp_path)

    assert outcome == "recovery_submitted"
    assert scripts == [(WINDOW, ["out_reco1_sbnd", "out_caf_sbnd"], 1)]
    assert events == [
        ("test_params", {"-Osubmit.memory=": "16GB"}),
        ("dataset", "rec_ds"),
        ("test_launch",),
        ("dataset", "run3_raw_data_ongoing"),
    ]
    assert state.read(cfg, window_recovery.SECTION) == {WINDOW: {"recovery_dataset": "rec_ds", "submission_id": "9001"}}


def test_never_touches_last_split(monkeypatch, tmp_path):
    _, cfg, _, _, _ = run_case(monkeypatch, tmp_path)

    assert state.read(cfg, "last_split") is None


def test_input_dataset_is_restored_when_the_launch_fails(monkeypatch, tmp_path):
    outcome, cfg, events, _, _ = run_case(monkeypatch, tmp_path, fail_launch=True)

    assert outcome == "recovery_failed"
    assert events[-1] == ("dataset", "run3_raw_data_ongoing")
    assert state.read(cfg, window_recovery.SECTION) is None


def test_a_window_is_recovered_once(monkeypatch, tmp_path):
    outcome, _, events, scripts, _ = run_case(monkeypatch, tmp_path, recorded={WINDOW: {"recovery_dataset": "rec_ds"}})

    assert outcome == "already_handled"
    assert events == [] and scripts == []


def test_nothing_missing_is_recorded_and_launches_nothing(monkeypatch, tmp_path):
    outcome, cfg, events, _, _ = run_case(monkeypatch, tmp_path, script_result=(1.0, 1.0, None))

    assert outcome == "no_recovery_needed"
    assert events == []
    assert state.read(cfg, window_recovery.SECTION) == {WINDOW: {"recovery_dataset": None}}


@pytest.mark.parametrize("source", [None, {"command": COMMAND, "submission_id": 1, "is_test": True}])
def test_no_real_submission_means_no_window(monkeypatch, tmp_path, source):
    outcome, _, events, scripts, _ = run_case(monkeypatch, tmp_path, source=source)

    assert outcome == "no_window"
    assert events == [] and scripts == []


@pytest.mark.parametrize("change", [
    {"test_split_type": "new(window=1m,round=,fts=,localtime=,firsttime=,lasttime=)"},
    {"test_param_overrides": [["-Osubmit.subgroup=", "test"], ["-Oglobal.prodtype=", "test"]]},
])
def test_refuses_a_test_launch_that_would_split_or_rename_the_outputs(monkeypatch, tmp_path, change):
    outcome, _, events, scripts, _ = run_case(monkeypatch, tmp_path, stage={**SAFE_STAGE, **change})

    assert outcome == "unsafe_test_overrides"
    assert events == [] and scripts == []


def test_test_split_type_none_is_safe():
    assert window_recovery.test_launch_problems({**SAFE_STAGE, "test_split_type": "None"}) == []


def test_dry_run_counts_the_missing_files_and_writes_nothing(monkeypatch, tmp_path):
    outcome, cfg, events, scripts, sam = run_case(monkeypatch, tmp_path, dry_run=True)

    assert outcome == "dry_run"
    assert events == [] and scripts == []
    assert sam.queries == [
        f"defname: {WINDOW} and not (isparentof: (defname: out_reco1_sbnd) and isparentof: (defname: out_caf_sbnd))"
    ]
    assert state.read(cfg, window_recovery.SECTION) is None
