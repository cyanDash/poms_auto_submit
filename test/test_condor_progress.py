import subprocess

import condor_progress
from condor_progress import Progress, get_progress

# Real shape captured live 2026-08-30 against jobsub_job_id
# 29756425@jobsub04.fnal.gov: condor_q -G sbnd 29756425 -autoformat:h
# JobStatus DAG_NodesDone DAG_NodesTotal -- printed the header line
# repeatedly with the one real data line mixed in among the repeats (see
# docs/adr/0007-condor-q-primary-progress-source.md).
REAL_STDOUT = """\
JobStatus DAG_NodesDone DAG_NodesTotal DAG_NodesFailed
JobStatus DAG_NodesDone DAG_NodesTotal DAG_NodesFailed
JobStatus DAG_NodesDone DAG_NodesTotal DAG_NodesFailed
JobStatus DAG_NodesDone DAG_NodesTotal DAG_NodesFailed
2         1177          10002          0
JobStatus DAG_NodesDone DAG_NodesTotal DAG_NodesFailed
"""


def fake_run(stdout="", returncode=0):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
    return run


def test_get_progress_live_row(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 50 100 0\n"))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("live", 50.0, 50)


def test_get_progress_finished_on_jobstatus_completed(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("4 400 502 0\n"))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("finished", 79.68127490039841, 102)


def test_get_progress_finished_when_all_nodes_done(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 502 502 0\n"))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("finished", 100.0, 0)


def test_get_progress_no_data_on_header_only_output(monkeypatch):
    monkeypatch.setattr(
        condor_progress.subprocess, "run",
        fake_run("JobStatus DAG_NodesDone DAG_NodesTotal\n"),
    )

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("no_data")


def test_get_progress_no_data_on_empty_output(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run(""))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("no_data")


def test_get_progress_no_data_on_undefined_fields(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 undefined undefined undefined\n"))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("no_data")


def test_get_progress_error_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("", returncode=1))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("error")


def test_get_progress_error_on_timeout(monkeypatch):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="condor_q", timeout=30)
    monkeypatch.setattr(condor_progress.subprocess, "run", raise_timeout)

    assert get_progress("sbnd", "1@jobsub04.fnal.gov") == Progress("error")


def test_get_progress_error_on_unusable_jobsub_job_id():
    assert get_progress("sbnd", None) == Progress("error")
    assert get_progress("sbnd", "abc@jobsub04.fnal.gov") == Progress("error")


def test_get_progress_parses_real_repeated_header_output(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run(REAL_STDOUT))

    assert get_progress("sbnd", "29756425@jobsub04.fnal.gov") == Progress("live", 1177 / 10002 * 100, 8825)


def test_get_progress_targets_owning_schedd(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="2 1 2 0\n", stderr="")
    monkeypatch.setattr(condor_progress.subprocess, "run", run)

    get_progress("sbnd", "29756425@jobsub04.fnal.gov")

    cmd = calls[0]
    assert cmd[cmd.index("-name") + 1] == "jobsub04.fnal.gov"


def test_get_progress_omits_schedd_selector_without_schedd(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="2 1 2 0\n", stderr="")
    monkeypatch.setattr(condor_progress.subprocess, "run", run)

    get_progress("sbnd", "29756425")

    assert "-name" not in calls[0]




def test_get_progress_unfinished_subtracts_failed_nodes(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 100 502 7\n"))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov").unfinished == 395


def test_get_progress_unfinished_is_none_when_failed_is_undefined(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("2 100 502 undefined\n"))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov").unfinished is None


def test_get_progress_finished_on_jobstatus_removed(monkeypatch):
    monkeypatch.setattr(condor_progress.subprocess, "run", fake_run("3 10 502 0\n"))

    assert get_progress("sbnd", "1@jobsub04.fnal.gov").outcome == "finished"
