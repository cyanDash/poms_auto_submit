import subprocess

import condor_rm


def test_remove_targets_owning_schedd_and_cluster(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(condor_rm.subprocess, "run", run)

    assert condor_rm.remove("sbnd", "29756425@jobsub04.fnal.gov") is True
    assert calls[0] == [condor_rm.CONDOR_RM_BIN, "-G", "sbnd", "-name", "jobsub04.fnal.gov", "29756425"]


def test_remove_false_on_nonzero_exit_timeout_and_bad_id(monkeypatch):
    monkeypatch.setattr(condor_rm.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no"))
    assert condor_rm.remove("sbnd", "1@s") is False

    def boom(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 30)
    monkeypatch.setattr(condor_rm.subprocess, "run", boom)
    assert condor_rm.remove("sbnd", "1@s") is False
    assert condor_rm.remove("sbnd", None) is False
