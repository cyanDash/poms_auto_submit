import poms_auto_submit as psc


def test_has_pro_subgroup_true_when_present():
    param_overrides = [["-Osubmit.subgroup=", "pro"], ["-Oglobal.sample=", "x"]]
    assert psc.has_pro_subgroup(param_overrides)


def test_has_pro_subgroup_false_when_absent():
    assert not psc.has_pro_subgroup([["-Oglobal.sample=", "x"]])


def test_has_pro_subgroup_false_when_subgroup_is_not_pro():
    assert not psc.has_pro_subgroup([["-Osubmit.subgroup=", "standard"]])


def test_plan_subgroups_one_slice_submits_pro_when_free():
    assert psc.plan_subgroups(1, pro_in_use=False, role="production") == [True]


def test_plan_subgroups_one_slice_submits_standard_when_pro_taken():
    assert psc.plan_subgroups(1, pro_in_use=True, role="production") == [False]


def test_plan_subgroups_two_slices_always_one_pro_one_standard():
    assert psc.plan_subgroups(2, pro_in_use=False, role="production") == [True, False]
    assert psc.plan_subgroups(2, pro_in_use=True, role="production") == [True, False]


def test_plan_subgroups_non_production_role_never_gets_pro():
    assert psc.plan_subgroups(1, pro_in_use=False, role="analysis") == [False]
    assert psc.plan_subgroups(2, pro_in_use=False, role="analysis") == [False, False]
