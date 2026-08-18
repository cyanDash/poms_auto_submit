import poms_auto_submit as psc


def test_plan_subgroups_one_slice_always_submits_pro():
    assert psc.plan_subgroups(1, role="production") == [True]


def test_plan_subgroups_two_slices_always_one_pro_one_standard():
    assert psc.plan_subgroups(2, role="production") == [True, False]


def test_plan_subgroups_non_production_role_never_gets_pro():
    assert psc.plan_subgroups(1, role="analysis") == [False]
    assert psc.plan_subgroups(2, role="analysis") == [False, False]
