import types

import pytest

from helpers import make_cfg
from poms_session import PomsSession

# Real response shape for a launch_jobs call (confirmed live, 2026-08-14):
# a redirect URL with submission_id in the query string, not "..._<digits>".
REAL_LAUNCH_JOBS_URL = (
    "https://pomsgpvm02.fnal.gov:9443/poms/list_launch_file/sbnd/analysis"
    "?campaign_stage_id=26938&submission_id=555"
)


def make_session(cfg=None, **pc_overrides):
    pc_overrides.setdefault("update_session_experiment", lambda experiment: None)
    pc_overrides.setdefault("update_session_role", lambda role: None)
    pc_overrides.setdefault("get_campaign_stage_id", lambda experiment, campaign_name, stage_name: 42)
    pc_overrides.setdefault(
        "submission_details",
        lambda experiment, role, submission_id: (True, {"submission": {"jobsub_job_id": None}}),
    )
    fake_pc = types.SimpleNamespace(**pc_overrides)
    return PomsSession(fake_pc, cfg or make_cfg())


def test_construction_sets_session_identity():
    calls = []
    session = make_session(
        update_session_experiment=lambda experiment: calls.append(("experiment", experiment)),
        update_session_role=lambda role: calls.append(("role", role)),
    )
    assert calls == [("experiment", "sbnd"), ("role", "production")]


def test_campaign_stage_id_is_resolved_once_and_cached():
    calls = []
    session = make_session(
        get_campaign_stage_id=lambda experiment, campaign_name, stage_name: calls.append(1) or 42,
    )
    assert session.campaign_stage_id == 42
    assert session.campaign_stage_id == 42
    assert len(calls) == 1


def test_get_progress_with_no_submissions_returns_empty_list():
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": []}},
        ),
    )

    assert session.get_progress() == []


def test_get_progress_picks_latest_submission_when_none_running():
    submissions = [
        {"submission_id": 100, "status": "Located"},
        {"submission_id": 102, "status": "Completed"},
        {"submission_id": 101, "status": "Located"},
    ]
    details_by_id = {
        102: {"submission_id": "102", "submission": {"pct_complete": 100.0, "jobsub_job_id": "111@jobsub01.fnal.gov"}},
    }
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details_by_id[submission_id]),
    )

    assert session.get_progress() == [
        {"submission_id": 102, "status": "Completed", "pct_complete": 100.0, "jobsub_job_id": "111@jobsub01.fnal.gov"}
    ]


def test_get_progress_returns_all_running_submissions():
    submissions = [
        {"submission_id": 100, "status": "Running"},
        {"submission_id": 102, "status": "Located"},
        {"submission_id": 101, "status": "Running"},
    ]
    details_by_id = {
        100: {"submission_id": "100", "submission": {"pct_complete": 10.0, "jobsub_job_id": "100@jobsub01.fnal.gov"}},
        101: {"submission_id": "101", "submission": {"pct_complete": 55.0, "jobsub_job_id": "101@jobsub01.fnal.gov"}},
    }
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details_by_id[submission_id]),
    )

    assert session.get_progress() == [
        {"submission_id": 100, "status": "Running", "pct_complete": 10.0, "jobsub_job_id": "100@jobsub01.fnal.gov"},
        {"submission_id": 101, "status": "Running", "pct_complete": 55.0, "jobsub_job_id": "101@jobsub01.fnal.gov"},
    ]


def test_get_progress_treats_held_as_active_alongside_running():
    # A Submission flips Running -> Held as soon as any job is held, even if
    # most of it is still progressing fine -- still in-flight, not dropped.
    submissions = [
        {"submission_id": 100, "status": "Held"},
        {"submission_id": 101, "status": "Running"},
        {"submission_id": 102, "status": "Located"},
    ]
    details_by_id = {
        100: {"submission_id": "100", "submission": {"pct_complete": 92.0, "jobsub_job_id": None}},
        101: {"submission_id": "101", "submission": {"pct_complete": 55.0, "jobsub_job_id": None}},
    }
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details_by_id[submission_id]),
    )

    assert session.get_progress() == [
        {"submission_id": 100, "status": "Held", "pct_complete": 92.0, "jobsub_job_id": None},
        {"submission_id": 101, "status": "Running", "pct_complete": 55.0, "jobsub_job_id": None},
    ]


def test_get_progress_treats_new_as_active_alongside_running_and_held():
    # New hasn't started progressing yet, but it's still in-flight (a slice
    # already queued), not idle/abandoned -- shouldn't be skipped over.
    submissions = [
        {"submission_id": 100, "status": "New"},
        {"submission_id": 101, "status": "Located"},
    ]
    details_by_id = {
        100: {"submission_id": "100", "submission": {"pct_complete": None, "jobsub_job_id": None}},
    }
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details_by_id[submission_id]),
    )

    assert session.get_progress() == [
        {"submission_id": 100, "status": "New", "pct_complete": None, "jobsub_job_id": None},
    ]


def test_get_progress_treats_idle_as_active_alongside_running_and_held():
    # Idle hasn't started progressing yet either, but it's queued and
    # in-flight -- same reasoning as New, shouldn't be skipped over.
    submissions = [
        {"submission_id": 100, "status": "Idle"},
        {"submission_id": 101, "status": "Located"},
    ]
    details_by_id = {
        100: {"submission_id": "100", "submission": {"pct_complete": None, "jobsub_job_id": None}},
    }
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details_by_id[submission_id]),
    )

    assert session.get_progress() == [
        {"submission_id": 100, "status": "Idle", "pct_complete": None, "jobsub_job_id": None},
    ]


def test_get_stage_params_finds_named_stage():
    stages = [
        {"name": "other_stage", "dataset": "other_dataset"},
        {"name": "test_stage", "dataset": "target_dataset"},
    ]
    session = make_session(show_campaign_stages=lambda campaign_name: (True, {"campaign_stages": stages}))

    assert session.get_stage_params() == {"name": "test_stage", "dataset": "target_dataset"}


def test_get_stage_params_raises_when_stage_not_found():
    session = make_session(
        show_campaign_stages=lambda campaign_name: (True, {"campaign_stages": [{"name": "other_stage"}]}),
    )

    with pytest.raises(RuntimeError):
        session.get_stage_params()


def test_update_stage_params_is_noop_when_no_updates():
    calls = []
    session = make_session(update_stage_param_overrides=lambda *a, **kw: calls.append((a, kw)))

    session.update_stage_params(updates={})

    assert calls == []


def test_update_stage_params_applies_param_overrides():
    # Real poms_client.update_stage_param_overrides() returns a single
    # stringified-tuple value on success, not an (ok, data) pair.
    calls = []

    def fake_update(experiment, campaign_stage, param_overrides=None):
        calls.append((experiment, campaign_stage, param_overrides))
        return "([('numjobs', '10')], None)"

    session = make_session(update_stage_param_overrides=fake_update)

    session.update_stage_params(updates={"numjobs": "10"})

    # requests' form-encoder flattens a dict *value* down to just its keys
    # (dropping the values), so param_overrides must be sent as a
    # pre-serialized Python-literal string, not a raw dict.
    assert calls == [("sbnd", 42, "[('numjobs', '10')]")]


def test_update_stage_params_raises_on_failure():
    # The server returns None when it can't find the campaign stage.
    session = make_session(update_stage_param_overrides=lambda *a, **kw: None)

    with pytest.raises(RuntimeError):
        session.update_stage_params(updates={"numjobs": "10"})


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
    session = make_session(
        get_campaign_stage_id=lambda experiment, campaign_name, stage_name: 26938,
        update_stage_param_overrides=lambda *a, **kw: REAL_UPDATE_STAGE_PARAM_OVERRIDES_RESPONSE,
    )

    # should not raise: a non-None response is treated as success
    session.update_stage_params(updates={"-Oglobal.neventsperjob=": "10"})


def test_set_subgroup_true_sets_pro_override():
    calls = []
    session = make_session(update_stage_param_overrides=lambda *a, **kw: calls.append(kw) or "(ok, None)")

    session.set_subgroup(True)

    assert calls == [{"param_overrides": "[('-Osubmit.subgroup=', 'pro')]"}]


def test_set_subgroup_false_clears_override():
    calls = []
    session = make_session(update_stage_param_overrides=lambda *a, **kw: calls.append(kw) or "(ok, None)")

    session.set_subgroup(False)

    assert calls == [{"param_overrides": "[('-Osubmit.subgroup=', '')]"}]


def test_submit_next_slice_returns_submission_id_on_success():
    session = make_session(make_poms_call=lambda **kw: (REAL_LAUNCH_JOBS_URL, 303))

    assert session.submit_next_slice() == "555"


def test_submit_next_slice_raises_on_non_303_status():
    session = make_session(make_poms_call=lambda **kw: ("some error", 500))

    with pytest.raises(RuntimeError):
        session.submit_next_slice()


def test_submit_next_slice_omits_test_launch_by_default():
    calls = []
    session = make_session(
        make_poms_call=lambda **kw: calls.append(kw) or (REAL_LAUNCH_JOBS_URL, 303)
    )

    session.submit_next_slice()

    assert calls[0]["test_launch"] is None


def test_submit_next_slice_passes_test_launch_when_enabled():
    calls = []
    session = make_session(
        cfg=make_cfg(test_launch=True),
        make_poms_call=lambda **kw: calls.append(kw) or (REAL_LAUNCH_JOBS_URL, 303),
    )

    session.submit_next_slice()

    assert calls[0]["test_launch"] == 1


def test_submit_next_slice_looks_up_jobsub_job_id():
    calls = []
    session = make_session(
        make_poms_call=lambda **kw: (REAL_LAUNCH_JOBS_URL, 303),
        submission_details=lambda experiment, role, submission_id: calls.append(submission_id)
        or (True, {"submission": {"jobsub_job_id": "71717566@jobsub03.fnal.gov"}}),
    )

    session.submit_next_slice()

    assert calls == ["555"]


def test_submit_next_slice_jobsub_job_id_lookup_failure_does_not_raise():
    session = make_session(
        make_poms_call=lambda **kw: (REAL_LAUNCH_JOBS_URL, 303),
        submission_details=lambda experiment, role, submission_id: (False, {}),
    )

    assert session.submit_next_slice() == "555"
