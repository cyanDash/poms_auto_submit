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


def test_plan_next_slices_tops_up_without_waiting_for_threshold():
    # target=2, one in-flight submission under threshold and not holding
    # pro -- top up by 1 immediately, and the new slice can take pro.
    session = FakeSession(submissions=make_submissions(sub(1, 40.0, subgroup="standard")))

    plan = psc.plan_next_slices(make_cfg(role="production", submit_two_slices=True), session)

    assert plan == [True]


def test_plan_next_slices_withholds_pro_when_already_held_in_flight():
    # Same as above, but the in-flight submission already holds pro -- the
    # new slice must go standard (only 1 pro submission in flight at a time).
    session = FakeSession(submissions=make_submissions(sub(1, 40.0, subgroup="pro")))

    plan = psc.plan_next_slices(make_cfg(role="production", submit_two_slices=True), session)

    assert plan == [False]
