import poms_auto_submit as psc


def test_plan_subgroups_one_slice_takes_pro_when_available():
    assert psc.plan_subgroups(1, role="production", pro_available=True) == [True]


def test_plan_subgroups_two_slices_one_pro_one_standard_when_available():
    assert psc.plan_subgroups(2, role="production", pro_available=True) == [True, False]


def test_plan_subgroups_non_production_role_never_gets_pro():
    assert psc.plan_subgroups(1, role="analysis", pro_available=True) == [False]
    assert psc.plan_subgroups(2, role="analysis", pro_available=True) == [False, False]


def test_plan_subgroups_no_pro_when_unavailable():
    # An in-flight submission already holds pro -- only one may hold it at a
    # time (see CONTEXT.md's Subgroup entry), so new slices all go standard.
    assert psc.plan_subgroups(1, role="production", pro_available=False) == [False]
    assert psc.plan_subgroups(2, role="production", pro_available=False) == [False, False]


def test_plan_subgroups_zero_slices_returns_empty():
    assert psc.plan_subgroups(0, role="production", pro_available=True) == []
