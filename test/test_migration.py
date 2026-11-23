import os
import shlex
import stat
import subprocess
import sys
import time
import types

import pytest

import migration
import state
from campaign_snapshot import CampaignSnapshot
from cleanup import run_cleanup
from helpers import duplicated_campaign, make_cfg

HOSTS = migration.HOSTS
DEFS = ["x_reco1_sbnd", "x_caf_sbnd", "x_flatcaf_sbnd"]
GOOD_LOG = "Done Mirroring Dataset!!!!\nFinished Sun Nov 8 18:11:27 CST 2026 exit=0\n"


@pytest.fixture(autouse=True)
def stub_poms_client(monkeypatch):
    monkeypatch.setitem(sys.modules, "poms_client", types.SimpleNamespace())


class FakeSsh:
    """Launches succeed on every host not in `down`; a launch claims the
    definition the way run_migration_job.sh does, unless claim=False."""

    def __init__(self, down=(), claim=True, down_for=0):
        self.down = set(down)
        self.claim = claim
        self.down_for = down_for  # every host fails this many attempts first
        self.no_token = set()
        self.launches = []
        self.attempts = []
        self.screens = {}

    def __call__(self, host, command):
        words = shlex.split(command)
        if words[0] == "screen" and words[1] == "-ls":
            if host in self.down:
                return 255, ""
            return 1, "".join(f"\t123.{s}\t(Detached)\n" for s in self.screens.get(host, ()))
        if words[0] == "htgettoken":
            self.attempts.append(host)
            if self.down_for > 0:
                self.down_for -= 1
                return 255, ""
            if host in self.down:
                return 255, ""
            return (1, "no vault token") if host in self.no_token else (0, "")
        assert words[:2] == ["screen", "-dmS"]
        session, definition, log = words[2], words[5], words[6]
        self.launches.append((definition, host))
        self.screens.setdefault(host, set()).add(session)
        if self.claim:
            os.symlink(host, f"{log}.claim")
        return 0, ""


class FakeTicket:
    def __init__(self):
        self.renewed = 0

    def renew(self):
        self.renewed += 1


class Counts:
    """`paths` maps a definition to one list of location paths per file."""

    def __init__(self, counts=None, error=None, paths=None, locate_error=None):
        self.counts = counts or {}
        self.error = error
        self.paths = paths or {}
        self.locate_error = locate_error

    def _files(self, defname):
        return self.paths.get(defname, [["/pnfs/sbn/data_add/x"]])

    def listFiles(self, defname=None):
        if self.locate_error:
            raise self.locate_error
        return [f"{defname}_{i}.root" for i in range(len(self._files(defname)))]

    def listFilesAndLocations(self, defname=None):
        return [
            (f"dcache:{path}", f"{defname}_{i}.root", "1")
            for i, paths in enumerate(self._files(defname)) for path in paths
        ]

    def countFiles(self, defname=None):
        if self.error:
            raise self.error
        return self.counts.get(defname, 10)


class DefinitionNotFound(Exception):
    pass


def make_migration_cfg(tmp_path, definitions=DEFS):
    cfg = make_cfg(
        cache_dir=str(tmp_path), state_file=str(tmp_path / "state_my_stage.json"),
        switch=True, do_cleanup=True, do_migration=True, experiment="sbnd",
    )
    state.write(cfg, "output_definitions", [{"name": d, "depth": 1} for d in definitions])
    state.write(cfg, "cleanup", {"finished": True})
    return cfg


def advance(cfg, ssh, sbnd=None, sbn=None, dry_run=False, sleeps=None):
    sleep = (sleeps.append if sleeps is not None else (lambda s: None))
    return migration.advance(
        cfg, dry_run=dry_run, ssh=ssh, ticket=FakeTicket(), sleep=sleep,
        sbnd=sbnd or Counts(), sbn=sbn or Counts(),
    )


def records(cfg):
    return state.read(cfg, "migration", {})


def finish(cfg, definition, text=GOOD_LOG):
    record = records(cfg)[definition]
    with open(record["log"], "w") as f:
        f.write(text)
    return record


# --- host selection ---

def test_one_definition_per_machine_in_order(tmp_path):
    cfg = make_migration_cfg(tmp_path)
    ssh = FakeSsh()
    advance(cfg, ssh)
    assert ssh.launches == list(zip(DEFS, HOSTS[:3]))
    assert all(records(cfg)[d]["status"] == "running" for d in DEFS)


def test_ssh_failure_moves_the_same_definition_to_the_next_machine(tmp_path):
    cfg = make_migration_cfg(tmp_path)
    ssh = FakeSsh(down={HOSTS[0], HOSTS[2]})
    advance(cfg, ssh)
    assert ssh.launches == [(DEFS[0], HOSTS[1]), (DEFS[1], HOSTS[3]), (DEFS[2], HOSTS[4])]


def test_machine_without_a_token_is_skipped_at_once(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    ssh.no_token = {HOSTS[0], HOSTS[1]}
    advance(cfg, ssh)
    assert ssh.launches == [("d1", HOSTS[2])]
    assert records(cfg)["d1"]["host"] == HOSTS[2]


def test_last_definition_circles_back_from_07_to_01(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1", "d2"])
    ssh = FakeSsh(down=set(HOSTS[:5]) | {HOSTS[6]})  # only 06 is up at first

    def recovers_after_07(host, command):
        result = ssh(host, command)
        if host == HOSTS[6]:
            ssh.down.discard(HOSTS[0])
        return result

    advance(cfg, recovers_after_07)
    assert ssh.attempts == list(HOSTS) + [HOSTS[0]]
    assert ssh.launches == [("d1", HOSTS[5]), ("d2", HOSTS[0])]


def test_keeps_circling_until_submitted_and_pauses_between_failed_passes(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh(down_for=len(HOSTS) + 2)
    sleeps = []
    advance(cfg, ssh, sleeps=sleeps)
    assert ssh.launches == [("d1", HOSTS[2])]
    assert sleeps == [migration.FAILED_PASS_WAIT_SECONDS]


def test_failed_pass_renews_the_ticket(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ticket = FakeTicket()
    migration.advance(
        cfg, ssh=FakeSsh(down_for=len(HOSTS)), ticket=ticket, sleep=lambda s: None,
        sbnd=Counts(), sbn=Counts(),
    )
    assert ticket.renewed == 1


def test_more_definitions_than_machines_wait_for_a_free_one(tmp_path):
    defs = [f"d{i}" for i in range(9)]
    cfg = make_migration_cfg(tmp_path, defs)
    ssh = FakeSsh()
    advance(cfg, ssh)
    assert [h for _, h in ssh.launches] == list(HOSTS)
    assert "d7" not in records(cfg) and "d8" not in records(cfg)


def test_busy_machine_is_skipped_on_a_later_run(tmp_path):
    cfg = make_migration_cfg(tmp_path, [f"d{i}" for i in range(8)])
    ssh = FakeSsh()
    advance(cfg, ssh)
    finish(cfg, "d2")
    ssh.screens[HOSTS[2]].clear()
    advance(cfg, ssh)
    assert ssh.launches[-1] == ("d7", HOSTS[2])


# --- polling ---

def test_running_job_is_left_alone(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    advance(cfg, ssh)
    assert len(ssh.launches) == 1
    assert records(cfg)["d1"]["status"] == "running"


def test_unreachable_host_with_a_started_job_stays_running(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    ssh.down.add(HOSTS[0])
    advance(cfg, ssh)
    assert len(ssh.launches) == 1
    assert records(cfg)["d1"]["status"] == "running"


def test_job_that_never_claimed_is_relaunched_on_the_next_machine(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh(claim=False)
    advance(cfg, ssh)
    ssh.screens.clear()
    ssh.claim = True
    advance(cfg, ssh)
    assert ssh.launches == [("d1", HOSTS[0]), ("d1", HOSTS[1])]


def test_screen_gone_after_claim_without_finish_is_failed_and_not_relaunched(tmp_path, caplog):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    ssh.screens.clear()
    advance(cfg, ssh)
    advance(cfg, ssh)
    assert len(ssh.launches) == 1
    assert records(cfg)["d1"]["status"] == "failed"
    assert state.read(cfg, "switch") is None
    assert "d1" in caplog.text


def test_claim_names_the_real_host_after_an_ambiguous_ssh_failure(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    record = records(cfg)["d1"]
    state.write(cfg, "migration", {"d1": {**record, "host": HOSTS[4]}})
    advance(cfg, ssh)
    assert records(cfg)["d1"]["status"] == "running"
    assert records(cfg)["d1"]["host"] == HOSTS[0]


# --- verification and the switch ---

def test_all_verified_turns_the_switch_off(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1", "d2"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    finish(cfg, "d1")
    advance(cfg, ssh)
    assert records(cfg)["d1"]["status"] == "done"
    assert state.read(cfg, "switch") is None
    finish(cfg, "d2")
    advance(cfg, ssh)
    assert state.read(cfg, "switch") is False
    assert cfg["switch"] is False


@pytest.mark.parametrize("text", [
    "Done Mirroring Dataset!!!!\nFinished x exit=1\n",
    "File not mirrored:  -> a.root\nDone Mirroring Dataset!!!!\nFinished x exit=0\n",
    "sbn.migrate field error:  -> a.root\nDone Mirroring Dataset!!!!\nFinished x exit=0\n",
    "Finished x exit=0\n",
])
def test_bad_log_is_failed(tmp_path, text):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    finish(cfg, "d1", text)
    advance(cfg, ssh)
    assert records(cfg)["d1"]["status"] == "failed"
    assert state.read(cfg, "switch") is None


@pytest.mark.parametrize("sbnd,sbn", [
    (Counts({"d1": 10}), Counts({"d1": 9})),
    (Counts({"d1": 0}), Counts({"d1": 0})),
    (Counts(), Counts(error=DefinitionNotFound("no such definition"))),
])
def test_count_mismatch_or_missing_definition_is_failed(tmp_path, sbnd, sbn):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    finish(cfg, "d1")
    advance(cfg, ssh, sbnd=sbnd, sbn=sbn)
    assert records(cfg)["d1"]["status"] == "failed"


def test_sam_outage_during_verification_retries_next_run(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    finish(cfg, "d1")
    advance(cfg, ssh, sbn=Counts(error=OSError("SAM down")))
    assert records(cfg)["d1"]["status"] == "running"
    advance(cfg, ssh)
    assert records(cfg)["d1"]["status"] == "done"


def test_dry_run_writes_nothing_and_starts_nothing(tmp_path):
    cfg = make_migration_cfg(tmp_path)
    ssh = FakeSsh()
    advance(cfg, ssh, dry_run=True)
    assert ssh.attempts == []
    assert records(cfg) == {}
    assert state.read(cfg, "switch") is None


def test_dry_run_does_not_record_a_finished_definition(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    finish(cfg, "d1")
    advance(cfg, ssh, dry_run=True)
    assert records(cfg)["d1"]["status"] == "running"
    assert state.read(cfg, "switch") is None


def test_ticket_failure_starts_nothing(tmp_path, caplog):
    cfg = make_migration_cfg(tmp_path)
    ssh = FakeSsh()

    def no_ticket():
        raise migration.TicketError("kinit failed")

    assert migration.advance(cfg, ssh=ssh, ticket_factory=no_ticket, sbnd=Counts(), sbn=Counts()) is False
    assert ssh.attempts == []
    assert "kinit failed" in caplog.text


def test_non_sbnd_experiment_starts_nothing(tmp_path, caplog):
    cfg = make_migration_cfg(tmp_path)
    cfg["experiment"] = "icarus"
    ssh = FakeSsh()
    assert advance(cfg, ssh) is False
    assert ssh.attempts == []


# --- files must be off scratch before launch ---

SCRATCH = "/pnfs/sbnd/scratch/users/sbndpro/x"
PERSISTENT = "/pnfs/sbnd/persistent/users/sbndpro/x"


def test_definition_on_scratch_is_held_and_the_others_start(tmp_path, caplog):
    cfg = make_migration_cfg(tmp_path, ["d1", "d2"])
    ssh = FakeSsh()
    assert advance(cfg, ssh, sbnd=Counts(paths={"d1": [[SCRATCH], [SCRATCH]]})) is False
    assert ssh.launches == [("d2", HOSTS[0])]
    assert "d1" not in records(cfg)
    assert "2 of 2 file(s) are only on scratch" in caplog.text
    assert state.read(cfg, "switch") is None


def test_one_scratch_only_file_holds_the_whole_definition(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh, sbnd=Counts(paths={"d1": [[PERSISTENT], [SCRATCH]]}))
    assert ssh.attempts == []


def test_file_on_both_scratch_and_persistent_can_start(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh, sbnd=Counts(paths={"d1": [[SCRATCH, PERSISTENT]]}))
    assert ssh.launches == [("d1", HOSTS[0])]


def test_held_definition_starts_once_moved_to_persistent(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh, sbnd=Counts(paths={"d1": [[SCRATCH]]}))
    advance(cfg, ssh, sbnd=Counts(paths={"d1": [[PERSISTENT]]}))
    assert ssh.launches == [("d1", HOSTS[0])]


def test_empty_definition_is_held(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    assert advance(cfg, ssh, sbnd=Counts(paths={"d1": []})) is False
    assert ssh.attempts == []


def test_sam_error_during_the_location_check_holds_and_retries(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    assert advance(cfg, ssh, sbnd=Counts(locate_error=OSError("SAM down"))) is False
    assert ssh.attempts == []
    advance(cfg, ssh)
    assert ssh.launches == [("d1", HOSTS[0])]


def test_dry_run_reports_a_held_definition(tmp_path, caplog):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    assert advance(cfg, ssh, sbnd=Counts(paths={"d1": [[SCRATCH]]}), dry_run=True) is False
    assert "only on scratch" in caplog.text
    assert "would start" not in caplog.text
    assert records(cfg) == {}


def test_started_definition_is_not_rechecked(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["d1"])
    ssh = FakeSsh()
    advance(cfg, ssh)
    advance(cfg, ssh, sbnd=Counts(paths={"d1": [[SCRATCH]]}))
    assert records(cfg)["d1"]["status"] == "running"


# --- gating ---

def snapshot(cfg):
    return CampaignSnapshot(cfg, [], lambda *a: None)


def test_migration_ready_after_cleanup_finished(tmp_path):
    cfg = make_migration_cfg(tmp_path)
    cfg.update(max_splits=5, last_split=5)
    assert snapshot(cfg).migration_ready() is True


@pytest.mark.parametrize("change", [
    {"do_migration": False}, {"do_cleanup": False}, {"last_split": 4},
])
def test_migration_not_ready(tmp_path, change):
    cfg = make_migration_cfg(tmp_path)
    cfg.update(max_splits=5, last_split=5)
    cfg.update(change)
    assert snapshot(cfg).migration_ready() is False


def test_migration_waits_for_cleanup_to_finish(tmp_path):
    cfg = make_migration_cfg(tmp_path)
    cfg.update(max_splits=5, last_split=5)
    state.write(cfg, "cleanup", {"finished": False})
    assert snapshot(cfg).migration_ready() is False


def test_finished_cleanup_does_not_run_again_when_migration_is_on(tmp_path):
    cfg = make_migration_cfg(tmp_path)
    cfg.update(max_splits=5, last_split=5, do_recovery=False)
    assert CampaignSnapshot(cfg, [{"submission_id": 1}], lambda *a: None).cleanup_ready() is False


def test_cleanup_leaves_the_switch_on_when_migration_is_on(tmp_path):
    cfg = make_migration_cfg(tmp_path, ["x_reco1_sbnd", "x_larcvreco1_sbnd"])
    state.write(cfg, "cleanup", {})
    assert run_cleanup(cfg, None, sam=duplicated_campaign()) is True
    assert state.read(cfg, "cleanup")["finished"] is True
    assert cfg["switch"] is True
    assert state.read(cfg, "switch") is None


# --- the remote job script ---

def _fake_bin(path, name, body):
    tool = path / name
    tool.write_text("#!/bin/bash\n" + body)
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)


def _run_job(tmp_path, htgettoken_rc=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    _fake_bin(bin_dir, "htgettoken", f"exit {htgettoken_rc}\n")
    _fake_bin(bin_dir, "hostname", "echo gpvm.test\n")
    refresh_pid = tmp_path / "refresh.pid"
    (mirror / "refresh_token_loop.sh").write_text(f"echo $$ > {refresh_pid}.tmp; mv {refresh_pid}.tmp {refresh_pid}; sleep 30\n")
    (mirror / "run_migration_container.sh").write_text(
        f'until [ -s {refresh_pid} ]; do sleep 0.05; done; echo "$1 ran" >> "$2"\n'
    )
    log = tmp_path / "d1.log"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "MIRROR_DIR": str(mirror)}
    job = os.path.join(os.path.dirname(migration.__file__), "run_migration_job.sh")
    result = subprocess.run(["bash", job, "d1", str(log)], env=env, timeout=20)
    return result, log


def test_job_claims_with_its_host_and_runs_the_migration(tmp_path):
    result, log = _run_job(tmp_path)
    assert result.returncode == 0
    assert os.readlink(f"{log}.claim") == "gpvm.test"
    assert log.read_text() == "d1 ran\n"
    refresh_pid = int((tmp_path / "refresh.pid").read_text())
    for _ in range(40):
        if not os.path.exists(f"/proc/{refresh_pid}"):
            break
        time.sleep(0.05)
    assert not os.path.exists(f"/proc/{refresh_pid}")


def test_job_without_a_token_does_not_claim(tmp_path):
    result, log = _run_job(tmp_path, htgettoken_rc=1)
    assert result.returncode != 0
    assert not os.path.lexists(f"{log}.claim")
    assert not log.exists()


def test_job_refuses_an_already_claimed_definition(tmp_path):
    os.symlink("other.host", tmp_path / "d1.log.claim")
    result, log = _run_job(tmp_path)
    assert result.returncode != 0
    assert os.readlink(f"{log}.claim") == "other.host"
    assert not log.exists()
