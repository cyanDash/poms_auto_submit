import json
import types
from datetime import datetime

import pytest

import poms_session
from helpers import make_cfg
from poms_session import PomsSession

# poms_session.raw_poms_call is imported by name into this module (`from
# poms_raw_client import raw_poms_call`), so submit_next_slice() tests
# monkeypatch it there -- raw_poms_call's own auth/POST plumbing is
# test_poms_raw_client.py's concern, not this file's.


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    # submit_next_slice() polls, sleeping JOBSUB_ID_POLL_SECONDS between
    # attempts, until POMS assigns a jobsub_job_id -- don't actually block
    # the test suite on that.
    monkeypatch.setattr(poms_session.time, "sleep", lambda seconds: None)


# Real response shape for a launch_jobs call (confirmed live, 2026-08-14):
# a redirect URL with submission_id in the query string, not "..._<digits>".
REAL_LAUNCH_JOBS_URL = (
    "https://pomsgpvm02.fnal.gov:9443/poms/list_launch_file/sbnd/analysis"
    "?campaign_stage_id=26938&submission_id=555"
)

# Real launch_jobs failure body once a campaign stage's Input Dataset is
# exhausted (confirmed live, campaign_stage_id=26971, decode_reco1_reco2_caf,
# 2026-09-02) -- see docs/poms_client_gotchas.md.
REAL_NO_MORE_SPLITS_BODY = "Unknown error AssertionError('No more splits in this campaign.')"


def make_session(cfg=None, **pc_overrides):
    pc_overrides.setdefault("update_session_experiment", lambda experiment: None)
    pc_overrides.setdefault("update_session_role", lambda role: None)
    pc_overrides.setdefault("get_campaign_stage_id", lambda experiment, campaign_name, stage_name: 42)
    pc_overrides.setdefault(
        "submission_details",
        lambda experiment, role, submission_id: (True, {"submission": {"jobsub_job_id": "default-job-id"}}),
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
        {
            "submission_id": 102, "status": "Completed", "pct_complete": 100.0,
            "jobsub_job_id": "111@jobsub01.fnal.gov", "subgroup": None,
            "last_status_change": None, "files_submitted": None, "files_pending": None,
        }
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
        {
            "submission_id": 100, "status": "Running", "pct_complete": 10.0,
            "jobsub_job_id": "100@jobsub01.fnal.gov", "subgroup": None,
            "last_status_change": None, "files_submitted": None, "files_pending": None,
        },
        {
            "submission_id": 101, "status": "Running", "pct_complete": 55.0,
            "jobsub_job_id": "101@jobsub01.fnal.gov", "subgroup": None,
            "last_status_change": None, "files_submitted": None, "files_pending": None,
        },
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
        {
            "submission_id": 100, "status": "Held", "pct_complete": 92.0, "jobsub_job_id": None, "subgroup": None,
            "last_status_change": None, "files_submitted": None, "files_pending": None,
        },
        {
            "submission_id": 101, "status": "Running", "pct_complete": 55.0, "jobsub_job_id": None, "subgroup": None,
            "last_status_change": None, "files_submitted": None, "files_pending": None,
        },
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
        {
            "submission_id": 100, "status": "New", "pct_complete": None, "jobsub_job_id": None, "subgroup": None,
            "last_status_change": None, "files_submitted": None, "files_pending": None,
        },
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
        {
            "submission_id": 100, "status": "Idle", "pct_complete": None, "jobsub_job_id": None, "subgroup": None,
            "last_status_change": None, "files_submitted": None, "files_pending": None,
        },
    ]


def test_get_progress_parses_subgroup_from_command_executed():
    # subgroup isn't a flat field -- it only shows up inside the literal
    # jobsub command POMS actually ran (confirmed live 2026-08-26, see
    # docs/poms_client_gotchas.md). The stage's *current* param_overrides
    # get overwritten by later runs and can't be trusted for this.
    submissions = [{"submission_id": 100, "status": "Running"}]
    details = {
        "submission_id": "100",
        "submission": {
            "pct_complete": 10.0,
            "jobsub_job_id": "100@jobsub01.fnal.gov",
            "command_executed": "jobsub_submit ... --group=sbnd --subgroup=pro --role=production ...",
        },
    }
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details),
    )

    assert session.get_progress()[0]["subgroup"] == "pro"


def test_get_progress_subgroup_is_none_when_command_executed_is_missing():
    submissions = [{"submission_id": 100, "status": "Running"}]
    details = {"submission_id": "100", "submission": {"pct_complete": 10.0, "jobsub_job_id": None}}
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details),
    )

    assert session.get_progress()[0]["subgroup"] is None


def test_get_progress_parses_last_status_change_and_file_counts():
    # history/statuses are siblings of "submission" in the real response
    # (confirmed live 2026-08-30, submission 3136636 -- see
    # docs/poms_client_gotchas.md). last_status_change is the max history
    # timestamp; files_submitted/files_pending come from the statuses[]
    # [label, count, url] triples -- fallback layer 2 in
    # docs/adr/0007-condor-q-primary-progress-source.md.
    submissions = [{"submission_id": 100, "status": "Running"}]
    details = {
        "submission_id": "100",
        "submission": {"pct_complete": 0.04, "jobsub_job_id": None},
        "history": [
            {"created": "2026-08-28T09:03:36", "status_id": 4000},
            {"created": "2026-08-28T11:37:25", "status_id": 4000},
        ],
        "statuses": [
            ["Available output: ", 38804, "url"],
            ["Submitted to SAM: ", 10000, "url"],
            ["Consumed by SAM: ", 9982, "url"],
            ["Pending: ", 299, "url"],
        ],
    }
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details),
    )

    entry = session.get_progress()[0]

    assert entry["last_status_change"] == datetime(2026, 8, 28, 11, 37, 25)
    assert entry["files_submitted"] == 10000
    assert entry["files_pending"] == 299


def test_get_progress_last_status_change_and_file_counts_are_none_when_absent():
    submissions = [{"submission_id": 100, "status": "Running"}]
    details = {"submission_id": "100", "submission": {"pct_complete": 10.0, "jobsub_job_id": None}}
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True,
            {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details),
    )

    entry = session.get_progress()[0]

    assert entry["last_status_change"] is None
    assert entry["files_submitted"] is None
    assert entry["files_pending"] is None


# --- static-field cache -- see docs/adr/0008-cache-static-submission-fields.md ---

def test_get_progress_uses_cache_and_skips_submission_details(tmp_path):
    submissions = [{"submission_id": 100, "status": "Running"}]
    (tmp_path / "submission_cache_42.json").write_text(
        json.dumps({"100": {"jobsub_job_id": "cached@jobsub01.fnal.gov", "subgroup": "pro"}})
    )
    calls = []
    session = make_session(
        cfg=make_cfg(cache_dir=str(tmp_path)),
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True, {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: calls.append(submission_id) or (True, {}),
    )

    entry = session.get_progress()[0]

    assert calls == []
    assert entry["jobsub_job_id"] == "cached@jobsub01.fnal.gov"
    assert entry["subgroup"] == "pro"
    assert entry["pct_complete"] is None
    assert entry["last_status_change"] is None
    assert entry["files_submitted"] is None
    assert entry["files_pending"] is None


def test_get_progress_cache_miss_fetches_and_writes_cache(tmp_path):
    submissions = [{"submission_id": 100, "status": "Running"}]
    details = {
        "submission_id": "100",
        "submission": {
            "pct_complete": 10.0,
            "jobsub_job_id": "100@jobsub01.fnal.gov",
            "command_executed": "jobsub_submit ... --subgroup=pro ...",
        },
    }
    session = make_session(
        cfg=make_cfg(cache_dir=str(tmp_path)),
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True, {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: (True, details),
    )

    entry = session.get_progress()[0]

    assert entry["jobsub_job_id"] == "100@jobsub01.fnal.gov"
    assert entry["subgroup"] == "pro"
    assert entry["pct_complete"] == 10.0

    written = json.loads((tmp_path / "submission_cache_42.json").read_text())
    assert written == {"100": {"jobsub_job_id": "100@jobsub01.fnal.gov", "subgroup": "pro"}}


def test_get_progress_only_fetches_uncached_submissions(tmp_path):
    submissions = [
        {"submission_id": 100, "status": "Running"},
        {"submission_id": 101, "status": "Running"},
    ]
    (tmp_path / "submission_cache_42.json").write_text(
        json.dumps({"100": {"jobsub_job_id": "cached@jobsub01.fnal.gov", "subgroup": None}})
    )
    calls = []
    details = {"submission_id": "101", "submission": {"pct_complete": 5.0, "jobsub_job_id": "101@jobsub01.fnal.gov"}}
    session = make_session(
        cfg=make_cfg(cache_dir=str(tmp_path)),
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True, {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: calls.append(submission_id) or (True, details),
    )

    result = session.get_progress()

    assert calls == [101]
    assert result[0]["jobsub_job_id"] == "cached@jobsub01.fnal.gov"
    assert result[1]["jobsub_job_id"] == "101@jobsub01.fnal.gov"

    written = json.loads((tmp_path / "submission_cache_42.json").read_text())
    assert written == {
        "100": {"jobsub_job_id": "cached@jobsub01.fnal.gov", "subgroup": None},
        "101": {"jobsub_job_id": "101@jobsub01.fnal.gov", "subgroup": None},
    }


def test_get_jobsub_job_id_writes_cache_on_success(tmp_path):
    session = make_session(
        cfg=make_cfg(cache_dir=str(tmp_path)),
        submission_details=lambda experiment, role, submission_id: (
            True,
            {"submission": {"jobsub_job_id": "71717566@jobsub03.fnal.gov", "command_executed": "... --subgroup=standard ..."}},
        ),
    )

    session._get_jobsub_job_id("555")

    written = json.loads((tmp_path / "submission_cache_42.json").read_text())
    assert written == {"555": {"jobsub_job_id": "71717566@jobsub03.fnal.gov", "subgroup": "standard"}}


def test_cache_is_noop_without_cache_dir():
    # make_cfg() sets no cache_dir -- every existing test relies on this:
    # submission_details() is always called fresh, nothing is written to disk.
    calls = []
    submissions = [{"submission_id": 100, "status": "Running"}]
    session = make_session(
        campaign_stage_submissions=lambda experiment, role, campaign_name, stage_name: (
            True, {"data": {"submissions": submissions}},
        ),
        submission_details=lambda experiment, role, submission_id: calls.append(submission_id)
        or (True, {"submission": {"pct_complete": 1.0, "jobsub_job_id": "x"}}),
    )

    session.get_progress()
    session.get_progress()

    assert calls == [100, 100]
    assert session.cache_file is None


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


def fake_raw_poms_call(monkeypatch, result):
    """result: (res, status) to return, or a callable(pc, method, **kw) -> (res, status)."""
    fn = result if callable(result) else (lambda pc, method, **kw: result)
    monkeypatch.setattr(poms_session, "raw_poms_call", fn)


def test_set_recovery_input_dataset_sends_dataset_and_reset_split(monkeypatch):
    calls = []
    fake_raw_poms_call(monkeypatch, lambda pc, method, **kw: calls.append((method, kw)) or ("ok", 200))
    session = make_session()

    session.set_recovery_input_dataset("my_recovery_dataset")

    method, kwargs = calls[0]
    assert method == "update_campaign_stage"
    assert kwargs["campaign_stage"] == 42
    assert kwargs["dataset"] == "my_recovery_dataset"
    assert kwargs["cs_last_split"] == 0


def test_set_recovery_input_dataset_raises_on_failure(monkeypatch):
    fake_raw_poms_call(monkeypatch, ("some real error text", 500))
    session = make_session()

    with pytest.raises(RuntimeError, match="some real error text"):
        session.set_recovery_input_dataset("my_recovery_dataset")


def test_submit_next_slice_returns_submission_id_on_success(monkeypatch):
    fake_raw_poms_call(monkeypatch, (REAL_LAUNCH_JOBS_URL, 303))
    session = make_session()

    assert session.submit_next_slice() == "555"


def test_submit_next_slice_raises_with_real_body_on_other_failure(monkeypatch):
    fake_raw_poms_call(monkeypatch, ("some real unmangled error text", 500))
    session = make_session()

    with pytest.raises(RuntimeError, match="some real unmangled error text"):
        session.submit_next_slice()


def test_submit_next_slice_returns_none_when_no_more_splits(monkeypatch):
    # Confirmed real body once a campaign stage's Input Dataset is exhausted
    # -- treated as graceful completion, not a failure; see
    # docs/poms_client_gotchas.md.
    fake_raw_poms_call(monkeypatch, (REAL_NO_MORE_SPLITS_BODY, 400))
    session = make_session()

    assert session.submit_next_slice() is None


def test_submit_next_slice_omits_test_launch_by_default(monkeypatch):
    calls = []
    fake_raw_poms_call(monkeypatch, lambda pc, method, **kw: calls.append(kw) or (REAL_LAUNCH_JOBS_URL, 303))
    session = make_session()

    session.submit_next_slice()

    assert calls[0]["test_launch"] is None


def test_submit_next_slice_passes_test_launch_when_enabled(monkeypatch):
    calls = []
    fake_raw_poms_call(monkeypatch, lambda pc, method, **kw: calls.append(kw) or (REAL_LAUNCH_JOBS_URL, 303))
    session = make_session(cfg=make_cfg(test_launch=True))

    session.submit_next_slice()

    assert calls[0]["test_launch"] == 1


def test_submit_next_slice_waits_before_looking_up_jobsub_job_id(monkeypatch):
    calls = []
    monkeypatch.setattr(poms_session.time, "sleep", lambda seconds: calls.append(("sleep", seconds)))
    fake_raw_poms_call(monkeypatch, (REAL_LAUNCH_JOBS_URL, 303))
    session = make_session(
        submission_details=lambda experiment, role, submission_id: calls.append(("submission_details",))
        or (True, {"submission": {"jobsub_job_id": "71717566@jobsub03.fnal.gov"}}),
    )

    session.submit_next_slice()

    assert calls == [("sleep", poms_session.JOBSUB_ID_POLL_SECONDS), ("submission_details",)]


def test_submit_next_slice_looks_up_jobsub_job_id(monkeypatch):
    calls = []
    fake_raw_poms_call(monkeypatch, (REAL_LAUNCH_JOBS_URL, 303))
    session = make_session(
        submission_details=lambda experiment, role, submission_id: calls.append(submission_id)
        or (True, {"submission": {"jobsub_job_id": "71717566@jobsub03.fnal.gov"}}),
    )

    session.submit_next_slice()

    assert calls == ["555"]


def test_submit_next_slice_polls_until_jobsub_job_id_is_assigned(monkeypatch):
    # First two lookups come back with no job id yet (not assigned), the
    # third has it -- submit_next_slice should keep polling rather than
    # giving up after the first miss.
    responses = [
        (True, {"submission": {"jobsub_job_id": None}}),
        (True, {"submission": {"jobsub_job_id": None}}),
        (True, {"submission": {"jobsub_job_id": "71717566@jobsub03.fnal.gov"}}),
    ]
    calls = []
    fake_raw_poms_call(monkeypatch, (REAL_LAUNCH_JOBS_URL, 303))
    session = make_session(
        submission_details=lambda experiment, role, submission_id: calls.append(1) or responses[len(calls) - 1],
    )

    assert session.submit_next_slice() == "555"
    assert len(calls) == 3


def test_submit_next_slice_keeps_polling_through_lookup_failures(monkeypatch):
    # A failed submission_details() call (ok=False, or an exception) doesn't
    # end the poll -- it's treated as "not assigned yet", same as a bare
    # None, and polling continues until a real job id shows up.
    responses = [(False, {}), (True, {"submission": {"jobsub_job_id": "71717566@jobsub03.fnal.gov"}})]
    calls = []
    fake_raw_poms_call(monkeypatch, (REAL_LAUNCH_JOBS_URL, 303))
    session = make_session(
        submission_details=lambda experiment, role, submission_id: calls.append(1) or responses[len(calls) - 1],
    )

    assert session.submit_next_slice() == "555"
    assert len(calls) == 2
