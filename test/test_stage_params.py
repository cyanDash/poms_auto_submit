import types

import pytest

import poms_auto_submit as psc
from helpers import make_cfg


def test_get_stage_params_finds_named_stage():
    stages = [
        {"name": "other_stage", "dataset": "other_dataset"},
        {"name": "test_stage", "dataset": "target_dataset"},
    ]
    fake_pc = types.SimpleNamespace(
        show_campaign_stages=lambda campaign_name: (True, {"campaign_stages": stages}),
    )

    stage = psc.get_stage_params(fake_pc, make_cfg())

    assert stage == {"name": "test_stage", "dataset": "target_dataset"}


def test_get_stage_params_raises_when_stage_not_found():
    fake_pc = types.SimpleNamespace(
        show_campaign_stages=lambda campaign_name: (True, {"campaign_stages": [{"name": "other_stage"}]}),
    )

    with pytest.raises(RuntimeError):
        psc.get_stage_params(fake_pc, make_cfg())


def test_update_stage_params_is_noop_when_no_updates():
    calls = []
    fake_pc = types.SimpleNamespace(
        update_stage_param_overrides=lambda *a, **kw: calls.append((a, kw)),
    )

    psc.update_stage_params(fake_pc, make_cfg(), campaign_stage_id=42, updates={})

    assert calls == []


def test_update_stage_params_applies_param_overrides():
    # Real poms_client.update_stage_param_overrides() returns a single
    # stringified-tuple value on success, not an (ok, data) pair.
    calls = []

    def fake_update(experiment, campaign_stage, param_overrides=None):
        calls.append((experiment, campaign_stage, param_overrides))
        return "([('numjobs', '10')], None)"

    fake_pc = types.SimpleNamespace(update_stage_param_overrides=fake_update)

    psc.update_stage_params(fake_pc, make_cfg(), campaign_stage_id=42, updates={"numjobs": "10"})

    # requests' form-encoder flattens a dict *value* down to just its keys
    # (dropping the values), so param_overrides must be sent as a
    # pre-serialized Python-literal string, not a raw dict.
    assert calls == [("sbnd", 42, "[('numjobs', '10')]")]


def test_update_stage_params_raises_on_failure():
    # The server returns None when it can't find the campaign stage.
    fake_pc = types.SimpleNamespace(
        update_stage_param_overrides=lambda *a, **kw: None,
    )

    with pytest.raises(RuntimeError):
        psc.update_stage_params(fake_pc, make_cfg(), campaign_stage_id=42, updates={"numjobs": "10"})


# Real response recorded 2026-08-14 from a live, idempotent
# update_stage_param_overrides call against campaign_stage_id=26938
# (sdas1's own HNL campaign, not shared production) -- setting
# -Oglobal.neventsperjob= back to its own current value "10". Pins down the
# real success-response shape server-side (see fermitools/poms's
# StagesPOMS.py: returns str((stage.param_overrides, stage.test_param_overrides))).
REAL_UPDATE_STAGE_PARAM_OVERRIDES_RESPONSE = (
    "([['--stage=', 'gen_g4_detsim_reco1_reco2_caf'], "
    "['-Oglobal.fclname=', 'prodMeVPrtl_hnl_mupi_m_300_Um4_1p00e7_TPC_sbnd'], "
    "['-Oglobal.fclfile1=', 'prodMeVPrtl_hnl_mupi_m_300_Um4_1p00e7_TPC_sbnd.fcl'], "
    "['-Oglobal.fclfile2=', 'standard_g4_sbnd.fcl'], "
    "['-Oglobal.fclfile3=', 'standard_detsim_sbnd.fcl'], "
    "['-Oglobal.fclfile4=', 'standard_reco1_sbnd.fcl'], "
    "['-Oglobal.fclfile5=', 'standard_reco2_sbnd.fcl '], "
    "['-Oglobal.fclfile6=', 'cafmakerjob_sbnd_sce_and_fluxwgt_and_g4rw.fcl'], "
    "['-Oglobal.neventsperjob=', '10'], "
    "['-Osubmit.N=', '1000'], "
    "['-Osubmit.maxConcurrent=', '1000'], "
    "['-Osubmit.memory=', '8GB'], "
    "['-Osubmit.disk=', '2GB'], "
    "['-Osubmit.expected-lifetime=', '8h']], None)"
)


def test_update_stage_params_handles_real_recorded_response():
    fake_pc = types.SimpleNamespace(
        update_stage_param_overrides=lambda *a, **kw: REAL_UPDATE_STAGE_PARAM_OVERRIDES_RESPONSE,
    )

    # should not raise: a non-None response is treated as success
    psc.update_stage_params(
        fake_pc, make_cfg(), campaign_stage_id=26938, updates={"-Oglobal.neventsperjob=": "10"}
    )


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
