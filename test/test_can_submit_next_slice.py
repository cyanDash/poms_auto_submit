import poms_slice_cron as psc
from helpers import make_cfg, make_progress, sub


def test_can_submit_next_slice_skips_when_active_count_unknown():
    num = psc.can_submit_next_slice(make_cfg(), make_progress(), active_count=None)
    assert num == 0


def test_can_submit_next_slice_bootstraps_single_target_when_nothing_active():
    num = psc.can_submit_next_slice(make_cfg(submit_two_slices=False), make_progress(), active_count=0)
    assert num == 1


def test_can_submit_next_slice_bootstraps_double_target_when_nothing_active():
    num = psc.can_submit_next_slice(make_cfg(submit_two_slices=True), make_progress(), active_count=0)
    assert num == 2


def test_can_submit_next_slice_skips_when_none_past_threshold():
    progress = make_progress(sub(1, 40.0))
    num = psc.can_submit_next_slice(make_cfg(submit_two_slices=True), progress, active_count=1)
    assert num == 0


def test_can_submit_next_slice_submits_one_when_single_target_and_ready():
    progress = make_progress(sub(1, 90.0))
    num = psc.can_submit_next_slice(make_cfg(submit_two_slices=False), progress, active_count=1)
    assert num == 1


def test_can_submit_next_slice_submits_two_when_only_running_slice_is_ready():
    progress = make_progress(sub(1, 90.0))
    num = psc.can_submit_next_slice(make_cfg(submit_two_slices=True), progress, active_count=1)
    assert num == 2


def test_can_submit_next_slice_submits_one_when_one_of_two_ready():
    progress = make_progress(sub(1, 90.0), sub(2, 40.0))
    num = psc.can_submit_next_slice(make_cfg(submit_two_slices=True), progress, active_count=2)
    assert num == 1


def test_can_submit_next_slice_submits_two_when_both_of_two_ready():
    progress = make_progress(sub(1, 90.0), sub(2, 95.0))
    num = psc.can_submit_next_slice(make_cfg(submit_two_slices=True), progress, active_count=2)
    assert num == 2
