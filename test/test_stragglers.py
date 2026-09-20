"""Straggler tracking and condor_rm removal; see docs/adr/0018."""

import json
import logging

import stragglers
from condor_progress import Progress
from helpers import make_cfg


def live(unfinished):
    return Progress("live", 90.0, unfinished)


FINISHED = Progress("finished", 100.0, 0)


class Env:
    def __init__(self, tmp_path):
        self.cfg = make_cfg(cache_dir=str(tmp_path), max_splits=1, last_split=1)
        self.tmp_path = tmp_path
        self.removed = []
        self.remove_result = True

    def run(self, progress, submissions=None, dry_run=False, stage_id=42):
        subs = submissions if submissions is not None else [entry(1)]
        stragglers.manage(
            self.cfg, stage_id, subs, dry_run=dry_run,
            get_progress=lambda e, j: progress[j],
            remove=lambda e, j: self.removed.append(j) or self.remove_result,
        )

    def records(self):
        with open(self.tmp_path / "stragglers_42.json") as f:
            return json.load(f)


def entry(submission_id, **extra):
    return {"submission_id": submission_id, "status": "Running", "subgroup": None,
            "jobsub_job_id": f"{submission_id}@s", **extra}


def test_more_than_50_unfinished_is_not_tracked(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(51)})
    assert env.records() == {}


def test_50_unfinished_starts_tracking_with_expected_minus_5(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(50)})
    r = env.records()["1"]
    assert (r["current_unfinished"], r["expected_unfinished"], r["stagger_count"]) == (50, 45, 0)


def test_drop_of_5_resets_counter_and_moves_expected(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(50)})
    env.run({"1@s": live(50)})
    assert env.records()["1"]["stagger_count"] == 1
    env.run({"1@s": live(45)})
    r = env.records()["1"]
    assert (r["stagger_count"], r["expected_unfinished"], r["current_unfinished"]) == (0, 40, 45)


def test_smaller_drop_or_no_change_increments(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(50)})
    env.run({"1@s": live(46)})
    env.run({"1@s": live(46)})
    assert env.records()["1"]["stagger_count"] == 2


def test_third_stalled_run_removes_once_with_job_id_and_drops_record(tmp_path, caplog):
    env = Env(tmp_path)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            env.run({"1@s": live(50)})
            assert env.removed == []
        env.run({"1@s": live(50)})
    assert env.removed == ["1@s"]
    assert env.records() == {}
    assert any("removed stalled Straggler" in r.getMessage() and "unfinished=50" in r.getMessage() for r in caplog.records)


def test_failed_removal_keeps_record_and_retries(tmp_path):
    env = Env(tmp_path)
    env.remove_result = False
    for _ in range(5):
        env.run({"1@s": live(50)})
    assert env.removed == ["1@s", "1@s"]
    assert "1" in env.records()


def test_failed_node_counts_as_progress(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(50)})
    env.run({"1@s": live(50)})
    env.run({"1@s": live(45)})  # e.g. a node moved into Failed
    assert env.records()["1"]["stagger_count"] == 0


def test_held_jobs_are_not_exempt(tmp_path):
    env = Env(tmp_path)
    sub = entry(1, status="Held")
    for _ in range(4):
        env.run({"1@s": live(50)}, [sub])
    assert env.removed == ["1@s"]


def test_record_dropped_when_finished_or_gone(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(50), "2@s": live(40)}, [entry(1), entry(2)])
    env.run({"1@s": FINISHED}, [entry(1)])
    assert env.records() == {}


def test_record_kept_when_count_rises_above_50(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(50)})
    env.run({"1@s": live(60)})
    assert env.records()["1"]["stagger_count"] == 1


def test_condor_q_error_changes_nothing(tmp_path):
    env = Env(tmp_path)
    env.run({"1@s": live(50)})
    before = env.records()
    env.run({"1@s": Progress("error")})
    assert env.records() == before


def test_dry_run_never_removes_and_leaves_records_alone(tmp_path, caplog):
    env = Env(tmp_path)
    for _ in range(3):
        env.run({"1@s": live(50)})
    before = env.records()
    with caplog.at_level(logging.WARNING):
        env.run({"1@s": live(50)}, dry_run=True)
    assert env.removed == []
    assert env.records() == before
    assert any("would condor_rm" in r.getMessage() for r in caplog.records)


def test_no_cache_dir_is_a_silent_noop(tmp_path, caplog):
    env = Env(tmp_path)
    env.cfg["cache_dir"] = None
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            env.run({"1@s": live(50)})
    assert env.removed == []
    assert caplog.records == []


def test_malformed_cache_file_is_tolerated(tmp_path):
    (tmp_path / "stragglers_42.json").write_text('{"1": "junk"}')
    env = Env(tmp_path)
    env.run({"1@s": live(50)})
    assert env.records()["1"]["stagger_count"] == 0


def test_outside_window_submission_warns_and_is_not_tracked(tmp_path, caplog):
    env = Env(tmp_path)
    with caplog.at_level(logging.WARNING):
        env.run({"1@s": live(10)}, [entry(1, outside_window=True)])
    assert env.records() == {}
    assert any("outside the 72h window" in r.getMessage() for r in caplog.records)


def test_removed_submission_no_longer_blocks_recovery_gate(tmp_path):
    import poms_auto_submit as psc
    env = Env(tmp_path)
    removed_dag = Progress("finished", 40.0, 30)  # JobStatus=3 after condor_rm
    assert psc._any_still_running(env.cfg, [dict(entry(1), status="Located")], lambda e, j: removed_dag) is False
