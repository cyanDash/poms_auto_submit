"""CampaignSnapshot: one reading of the stage's Submissions and condor_q per
run; docs/adr/0021."""

from campaign_snapshot import CampaignSnapshot
from condor_progress import Progress
from helpers import make_cfg

LIVE = Progress("live", 10.0)
ERROR = Progress("error")


def entry(submission_id, status="Running", jobsub_job_id=None):
    return {
        "submission_id": submission_id, "status": status, "subgroup": None,
        "jobsub_job_id": jobsub_job_id if jobsub_job_id is not None else f"{submission_id}@s",
    }


class FakeSession:
    def __init__(self, submissions):
        self.reads = 0
        self._submissions = submissions

    def get_progress(self):
        self.reads += 1
        return self._submissions


def test_reads_poms_once_and_condor_once_per_submission_across_every_question(tmp_path):
    queried = []
    cfg = make_cfg(
        do_cleanup=True, last_split=5, max_splits=5, cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"),
    )
    session = FakeSession([entry(1, "Completed"), entry(2, "Completed")])
    snapshot = CampaignSnapshot.take(cfg, session, lambda e, j: queried.append(j) or Progress("finished", 100.0))

    snapshot.cleanup_ready()
    snapshot.any_still_running()
    snapshot.recovery_ready()
    snapshot.slices_to_plan()
    snapshot.record_stuck()

    assert session.reads == 1
    assert queried == ["1@s", "2@s"]


def test_condor_q_is_not_queried_until_a_question_needs_it():
    queried = []
    cfg = make_cfg(last_split=5, max_splits=5)
    snapshot = CampaignSnapshot(cfg, [entry(1)], lambda e, j: queried.append(j) or LIVE)

    assert snapshot.slices_to_plan() == []  # no splits left

    assert queried == []


def test_condor_q_error_stops_querying_and_flags_the_snapshot():
    queried = []

    def lookup(e, j):
        queried.append(j)
        return ERROR

    snapshot = CampaignSnapshot(make_cfg(), [entry(1), entry(2)], lookup)

    assert snapshot.condor_error is True
    assert queried == ["1@s"]


def test_entries_pair_each_submission_with_its_progress():
    snapshot = CampaignSnapshot(make_cfg(), [entry(1), entry(2)], lambda e, j: LIVE)

    assert [(s["submission_id"], p) for s, p in snapshot.entries()] == [(1, LIVE), (2, LIVE)]


def test_record_stuck_does_nothing_when_no_splits_are_left(tmp_path):
    cfg = make_cfg(last_split=5, max_splits=5, cache_dir=str(tmp_path), state_file=str(tmp_path / "state.json"))
    snapshot = CampaignSnapshot(cfg, [entry(1, "Held")], lambda e, j: Progress("no_data"))

    snapshot.record_stuck()

    assert not list(tmp_path.glob("state*.json"))
