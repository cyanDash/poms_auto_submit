import poms_auto_submit as psc
from helpers import make_cfg, make_submissions, sub


class FakeSession:
    def __init__(self, submissions=None, active_count=0, pro_in_use=False):
        self._submissions = submissions or []
        self._active_count = active_count
        self._pro_in_use = pro_in_use
        self.pro_subgroup_in_use_calls = 0

    def get_progress(self):
        return self._submissions

    def get_active_submission_count(self):
        return self._active_count

    def pro_subgroup_in_use(self):
        self.pro_subgroup_in_use_calls += 1
        return self._pro_in_use


def test_plan_next_slices_short_circuits_when_nothing_to_submit():
    session = FakeSession(submissions=make_submissions(sub(1, 40.0)), active_count=1)

    plan = psc.plan_next_slices(make_cfg(), session)

    assert plan == []
    assert session.pro_subgroup_in_use_calls == 0


def test_plan_next_slices_bootstraps_with_subgroup_plan():
    session = FakeSession(active_count=0, pro_in_use=False)

    plan = psc.plan_next_slices(make_cfg(role="production"), session)

    assert plan == [True]
    assert session.pro_subgroup_in_use_calls == 1


def test_plan_next_slices_respects_max_splits_cap():
    session = FakeSession(active_count=0)

    plan = psc.plan_next_slices(make_cfg(submit_two_slices=True, last_split=4, max_splits=5), session)

    assert plan == [True]
