import poms_auto_submit as psc
from helpers import make_cfg, make_submissions, sub


def test_next_slice_count_bootstraps_single_target_when_nothing_submitted():
    num = psc.next_slice_count(make_cfg(submit_two_slices=False), make_submissions())
    assert num == 1


def test_next_slice_count_bootstraps_double_target_when_nothing_submitted():
    num = psc.next_slice_count(make_cfg(submit_two_slices=True), make_submissions())
    assert num == 2


def test_next_slice_count_tops_up_to_target_even_when_none_past_threshold():
    # target=2, one submission in flight (under threshold) -- top up to
    # target immediately rather than waiting for it to cross threshold first.
    submissions = make_submissions(sub(1, 40.0))
    num = psc.next_slice_count(make_cfg(submit_two_slices=True), submissions)
    assert num == 1


def test_next_slice_count_skips_when_in_flight_already_meets_target():
    submissions = make_submissions(sub(1, 40.0))
    num = psc.next_slice_count(make_cfg(submit_two_slices=False), submissions)
    assert num == 0


def test_next_slice_count_submits_one_when_single_target_and_ready():
    submissions = make_submissions(sub(1, 90.0))
    num = psc.next_slice_count(make_cfg(submit_two_slices=False), submissions)
    assert num == 1


def test_next_slice_count_submits_two_when_only_running_slice_is_ready():
    submissions = make_submissions(sub(1, 90.0))
    num = psc.next_slice_count(make_cfg(submit_two_slices=True), submissions)
    assert num == 2


def test_next_slice_count_submits_one_when_one_of_two_ready():
    submissions = make_submissions(sub(1, 90.0), sub(2, 40.0))
    num = psc.next_slice_count(make_cfg(submit_two_slices=True), submissions)
    assert num == 1


def test_next_slice_count_submits_two_when_both_of_two_ready():
    submissions = make_submissions(sub(1, 90.0), sub(2, 95.0))
    num = psc.next_slice_count(make_cfg(submit_two_slices=True), submissions)
    assert num == 2


def test_next_slice_count_skips_when_max_splits_reached():
    num = psc.next_slice_count(make_cfg(last_split=5, max_splits=5), make_submissions())
    assert num == 0


def test_next_slice_count_skips_when_last_split_past_max_splits():
    num = psc.next_slice_count(make_cfg(last_split=6, max_splits=5), make_submissions())
    assert num == 0


def test_next_slice_count_caps_to_remaining_splits_when_bootstrapping():
    num = psc.next_slice_count(make_cfg(submit_two_slices=True, last_split=4, max_splits=5), make_submissions())
    assert num == 1


def test_next_slice_count_caps_to_remaining_splits_when_both_ready():
    submissions = make_submissions(sub(1, 90.0), sub(2, 95.0))
    num = psc.next_slice_count(make_cfg(submit_two_slices=True, last_split=4, max_splits=5), submissions)
    assert num == 1


def test_next_slice_count_treats_none_pct_complete_as_in_flight():
    # A New/Idle submission hasn't started progressing yet (pct_complete is
    # None), but it's still occupying a slot, not free capacity.
    submissions = make_submissions({"submission_id": 1, "status": "New", "pct_complete": None, "subgroup": None})
    num = psc.next_slice_count(make_cfg(submit_two_slices=False), submissions)
    assert num == 0
