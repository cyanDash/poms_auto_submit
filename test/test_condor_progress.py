import subprocess

import condor_progress
from condor_progress import get_pct_complete

# Real shape captured live 2026-08-30 against jobsub_job_id
# 29756425@jobsub04.fnal.gov: condor_q -G sbnd 29756425 -autoformat:h
# JobStatus DAG_NodesDone DAG_NodesTotal -- printed the header line
# repeatedly with the one real data line mixed in among the repeats (see
# docs/adr/0007-condor-q-primary-progress-source.md).
REAL_STDOUT = """\
JobStatus DAG_NodesDone DAG_NodesTotal
JobStatus DAG_NodesDone DAG_NodesTotal
JobStatus DAG_NodesDone DAG_NodesTotal
JobStatus DAG_NodesDone DAG_NodesTotal
2         1177          10002
JobStatus DAG_NodesDone DAG_NodesTotal
"""


def fake_run(stdout="", returncode=0):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
    return run


def test_get_pct_complete_parses_real_repeated_header_output(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run(REAL_STDOUT))

    assert get_pct_complete("sbnd", "29756425@jobsub04.fnal.gov") == 1177 / 10002 * 100


def test_get_pct_complete_parses_single_clean_line(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 50 100\n"))

    assert get_pct_complete("sbnd", "1@jobsub04.fnal.gov") == 50.0


def test_get_pct_complete_none_when_dag_nodes_are_undefined(monkeypatch):
    # A non-DAGMan job, or one DAGMan hasn't started tracking yet.
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 undefined undefined\n"))

    assert get_pct_complete("sbnd", "1@jobsub04.fnal.gov") is None


def test_get_pct_complete_none_when_total_is_zero(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 0 0\n"))

    assert get_pct_complete("sbnd", "1@jobsub04.fnal.gov") is None


def test_get_pct_complete_none_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("", returncode=1))

    assert get_pct_complete("sbnd", "1@jobsub04.fnal.gov") is None


def test_get_pct_complete_none_on_subprocess_error(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="condor_q", timeout=30)
    monkeypatch.setattr(condor_progress.subprocess, "run", raise_timeout)

    assert get_pct_complete("sbnd", "1@jobsub04.fnal.gov") is None


def test_get_pct_complete_none_on_missing_jobsub_job_id():
    assert get_pct_complete("sbnd", None) is None
    assert get_pct_complete("sbnd", "") is None


def test_get_pct_complete_none_on_non_numeric_cluster_id():
    assert get_pct_complete("sbnd", "not-a-number@jobsub04.fnal.gov") is None


def test_get_pct_complete_none_on_unparseable_output(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("nothing useful here\n"))

    assert get_pct_complete("sbnd", "1@jobsub04.fnal.gov") is None
