import poms_auto_submit as psc
from helpers import make_cfg, make_submissions, sub


class FakeSession:
    def __init__(self, submissions=None):
        self._submissions = submissions or []

    def get_progress(self):
        return self._submissions


def test_plan_next_slices_returns_empty_when_nothing_to_submit():
    session = FakeSession(submissions=make_submissions(sub(1, 40.0)))

    plan = psc.plan_next_slices(make_cfg(), session)

    assert plan == []


def test_plan_next_slices_bootstraps_with_subgroup_plan():
    session = FakeSession()

    plan = psc.plan_next_slices(make_cfg(role="production"), session)

    assert plan == [True]


def test_plan_next_slices_respects_max_splits_cap():
    session = FakeSession()

    plan = psc.plan_next_slices(make_cfg(submit_two_slices=True, last_split=4, max_splits=5), session)

    assert plan == [True]
