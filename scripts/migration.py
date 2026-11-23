"""migration: mirrors a cleaned Campaign Stage's Output Definitions from SBND
SAM to SBN SAM, one per GPVM, then turns the stage off. advance() is the
module's one interface for the hourly run: it starts and polls detached jobs
and returns. See docs/adr/0027-sbn-migration-over-ssh-with-private-ticket.md.
"""

import hashlib
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time

import output_definitions
import sam_client
import state

SECTION = "migration"
HOSTS = tuple(f"sbndgpvm{n:02d}.fnal.gov" for n in range(1, 8))
FAILED_PASS_WAIT_SECONDS = 60
SSH_TIMEOUT_SECONDS = 30
SSH_FAILED = 255
SOURCE_EXPERIMENT = "sbnd"
TARGET_EXPERIMENT = "sbn"
MINT_TOKEN_COMMAND = (
    "htgettoken --credkey=sbndpro/managedtokens/fifeutilgpvm01.fnal.gov "
    "-a htvaultprod.fnal.gov -i sbnd -r production --nooidc --nokerberos --nossh "
    '--vaulttokenfile=/tmp/vt_u$(id -u)-sbnd_production -o /tmp/bt_u$(id -u)_production < /dev/null'
)
JOB_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run_migration_job.sh")
LOG_ERRORS = ("File not mirrored", "sbn.migrate field error")
LOG_DONE = "Done Mirroring Dataset!!!!"
FINISHED_RE = re.compile(r"^Finished .* exit=(\d+)\s*$")

RUNNING = "running"
DONE = "done"
FAILED = "failed"
NOT_STARTED = "not_started"


class TicketError(RuntimeError):
    """The keytab could not produce a Kerberos ticket."""


class Ticket:
    """A Kerberos ticket from sbndpro's keytab in a private credential cache,
    so it cannot disturb any login session's ticket. Use with `with`."""

    def __init__(self):
        self._dir = None
        self._keytab = os.path.join(os.path.expanduser("~"), f"{os.getuid()}.keytab")
        self.env = None

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory(prefix="poms_migration_krb_")
        self.env = {**os.environ, "KRB5CCNAME": f"FILE:{self._dir.name}/cc"}
        try:
            self.renew()
        except TicketError:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        if self.env:
            subprocess.run(["kdestroy", "-q"], env=self.env, stderr=subprocess.DEVNULL, check=False)
        if self._dir:
            self._dir.cleanup()
        self._dir = None
        self.env = None

    def renew(self):
        listed = subprocess.run(
            ["klist", "-k", self._keytab], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, check=False,
        )
        lines = listed.stdout.splitlines()[3:]
        if listed.returncode or not lines or len(lines[0].split()) < 2:
            raise TicketError(f"no principal in keytab {self._keytab}")
        principal = lines[0].split()[1]
        if subprocess.run(["kinit", "-k", "-t", self._keytab, principal], env=self.env, check=False).returncode:
            raise TicketError(f"kinit with keytab {self._keytab} failed")

    def ssh(self, host, command):
        """(returncode, stdout) of `command` on `host`; SSH_FAILED on a timeout."""
        argv = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "GSSAPIDelegateCredentials=no", host, command,
        ]
        try:
            done = subprocess.run(
                argv, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, universal_newlines=True, timeout=SSH_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            return SSH_FAILED, ""
        return done.returncode, done.stdout


def _session(definition):
    # Definition names can exceed what screen accepts as a session name.
    return "sbnmig_" + hashlib.sha1(definition.encode()).hexdigest()[:12]


def _log_path(cfg, definition):
    return os.path.join(cfg["cache_dir"], "migration", f"{definition}.log")


def _owner(record):
    """The machine that claimed the definition, or None if none has."""
    try:
        return os.readlink(f"{record['log']}.claim")
    except OSError:
        return None


def _screen_alive(ssh, host, session):
    """True or False, or None when the machine cannot be asked."""
    rc, out = ssh(host, "screen -ls")
    if rc == SSH_FAILED:
        return None
    return re.search(rf"\b\d+\.{re.escape(session)}\b", out) is not None


def _read_outcome(path):
    """(finished, exit_code, saw_done, saw_error) from a job log."""
    exit_code, saw_done, saw_error = None, False, False
    try:
        with open(path, errors="replace") as f:
            for line in f:
                match = FINISHED_RE.match(line)
                if match:
                    exit_code = int(match.group(1))
                saw_done = saw_done or LOG_DONE in line
                saw_error = saw_error or any(error in line for error in LOG_ERRORS)
    except FileNotFoundError:
        pass
    return exit_code is not None, exit_code, saw_done, saw_error


def _verify(definition, exit_code, saw_done, saw_error, sbnd, sbn):
    """DONE or FAILED, or RUNNING when SAM could not be asked."""
    if exit_code != 0 or not saw_done or saw_error:
        logging.error(
            "migration: %s: job log is not clean (exit=%s, finished mirroring=%s, file errors=%s)",
            definition, exit_code, saw_done, saw_error,
        )
        return FAILED
    try:
        source = sbnd.countFiles(defname=definition)
        target = sbn.countFiles(defname=definition)
    except Exception as error:
        if type(error).__name__ == "DefinitionNotFound":
            logging.error("migration: %s: definition missing after the job: %s", definition, error)
            return FAILED
        logging.warning("migration: %s: SAM check failed (%s) -- will retry next run", definition, error)
        return RUNNING
    if source <= 0 or source != target:
        logging.error("migration: %s: %d file(s) in SBND, %d in SBN", definition, source, target)
        return FAILED
    logging.info("migration: %s: verified, %d file(s) in SBN", definition, target)
    return DONE


def _poll(definition, record, ssh, sbnd, sbn):
    """The record after looking at its job."""
    owner = _owner(record)
    if owner is None:
        logging.info("migration: %s: never started on %s -- will use another machine", definition, record["host"])
        return {**record, "status": NOT_STARTED}
    record = {**record, "host": owner}
    finished, exit_code, saw_done, saw_error = _read_outcome(record["log"])
    if finished:
        return {**record, "status": _verify(definition, exit_code, saw_done, saw_error, sbnd, sbn)}
    alive = _screen_alive(ssh, owner, record["session"])
    if alive is False:
        logging.error("migration: %s: job on %s ended without finishing", definition, owner)
        return {**record, "status": FAILED}
    if alive is None:
        logging.warning("migration: %s: could not reach %s -- will check next run", definition, owner)
    else:
        logging.info("migration: %s: still running on %s", definition, owner)
    return record


def _launch_all(cfg, records, pending, ssh, ticket, sleep):
    """Starts each pending definition on its own machine, walking 01..07 and
    around again past any SSH failure. Leaves the rest when no machine is free."""
    position = 0
    failures = 0
    while pending:
        definition = pending[0]
        previous = records.get(definition, {})
        if previous.get("status") == NOT_STARTED and failures == 0:
            position = HOSTS.index(previous["host"]) + 1 if previous["host"] in HOSTS else position
        busy = {r["host"] for r in records.values() if r["status"] == RUNNING}
        free = [h for h in HOSTS if h not in busy]
        if not free:
            logging.info("migration: every machine is busy, %d definition(s) wait for a later run", len(pending))
            return
        while HOSTS[position % len(HOSTS)] in busy:
            position += 1
        host = HOSTS[position % len(HOSTS)]
        position += 1

        log = _log_path(cfg, definition)
        session = _session(definition)
        record = {"host": host, "session": session, "log": log, "status": RUNNING}
        # screen -dm succeeds before the job runs, so check the token first.
        rc, out = ssh(host, MINT_TOKEN_COMMAND)
        if rc == 0:
            state.write(cfg, SECTION, {**records, definition: record})
            command = " ".join(shlex.quote(w) for w in ["screen", "-dmS", session, "bash", JOB_SCRIPT, definition, log])
            rc, out = ssh(host, command)
        if rc == 0:
            logging.info("migration: %s: started on %s, log %s", definition, host, log)
            records[definition] = record
            pending.pop(0)
            failures = 0
            continue

        logging.warning("migration: %s: could not start on %s (rc=%s) %s", definition, host, rc, out.strip())
        state.write(cfg, SECTION, records)
        failures += 1
        if failures >= len(free):
            logging.warning("migration: no machine reachable -- retrying in %d s", FAILED_PASS_WAIT_SECONDS)
            sleep(FAILED_PASS_WAIT_SECONDS)
            ticket.renew()
            failures = 0


def _advance_with(cfg, definitions, ticket, ssh, sbnd, sbn, sleep, dry_run):
    records = {}
    for definition, record in (state.read(cfg, SECTION, {}) or {}).items():
        polled = _poll(definition, record, ssh, sbnd, sbn) if record["status"] == RUNNING else record
        records[definition] = polled

    pending = [d for d in definitions if records.get(d, {}).get("status", NOT_STARTED) == NOT_STARTED]
    if dry_run:
        for definition in pending:
            logging.info("dry-run: would start the migration of %s on the next free machine", definition)
        return True

    os.makedirs(os.path.dirname(_log_path(cfg, "x")), exist_ok=True)
    state.write(cfg, SECTION, records)
    _launch_all(cfg, records, pending, ssh, ticket, sleep)

    failed = sorted(d for d in definitions if records.get(d, {}).get("status") == FAILED)
    for definition in failed:
        logging.error(
            "migration: %s FAILED, log %s -- needs a person; it is never relaunched, switch stays on",
            definition, records[definition]["log"],
        )
    if all(records.get(d, {}).get("status") == DONE for d in definitions):
        from poms_auto_submit import persist_switch

        persist_switch(cfg, False)
        cfg["switch"] = False
        logging.info("migration: all %d definition(s) verified -- switch turned off", len(definitions))
    return not failed


def advance(cfg, dry_run=False, ssh=None, ticket=None, ticket_factory=None, sleep=time.sleep, sbnd=None, sbn=None):
    """One hourly step: polls started jobs, starts the rest, and turns the
    switch off once every Output Definition is verified in SBN. A dry run
    only reads. Returns False when something needs attention."""
    if cfg["experiment"] != SOURCE_EXPERIMENT:
        logging.error("migration: only %s can be mirrored, not %s", SOURCE_EXPERIMENT, cfg["experiment"])
        return False
    definitions = [entry["name"] for entry in output_definitions.recorded_output_definitions(cfg)]
    if not definitions:
        logging.error("migration: no output definitions recorded -- switch stays on")
        return False
    sbnd = sam_client.get_sam_client(SOURCE_EXPERIMENT, sbnd)
    sbn = sam_client.get_sam_client(TARGET_EXPERIMENT, sbn)

    if ticket is not None:
        return _advance_with(cfg, definitions, ticket, ssh, sbnd, sbn, sleep, dry_run)
    try:
        with (ticket_factory or Ticket)() as held:
            return _advance_with(cfg, definitions, held, ssh or held.ssh, sbnd, sbn, sleep, dry_run)
    except (TicketError, OSError) as error:
        logging.error("migration: no Kerberos ticket (%s) -- nothing started, switch stays on", error)
        return False
