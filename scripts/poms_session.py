"""PomsSession: the seam to poms_client. Owns identity setup, campaign_stage_id
resolution, and normalizes poms_client's response shapes behind plain method
returns -- callers never see a raw (ok, data) tuple, bare string, or redirect
URL.
"""

import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import state
from poms_raw_client import raw_poms_call

SUBGROUP_OVERRIDE_KEY = "-Osubmit.subgroup="
PRO_SUBGROUP = "pro"

# See "Known upstream bugs" in docs/poms_client_gotchas.md -- make_poms_call()
# mangles this text away entirely before submit_next_slice() ever sees it;
# _raw_launch_jobs_call() bypasses it precisely so this check works.
NO_MORE_SPLITS_MARKER = "No more splits in"

# command_executed is the immutable per-submission record of the actual
# --subgroup= flag; param_overrides isn't (see docs/poms_client_gotchas.md).
SUBGROUP_COMMAND_PATTERN = re.compile(r"--subgroup=(\S+)")

# jobsub_job_id isn't assigned synchronously with launch_jobs; poll for it.
JOBSUB_ID_POLL_SECONDS = 5

# See CONTEXT.md's Status entry for why Held/New/Idle count as in-flight.
ACTIVE_SUBMISSION_STATUSES = {"New", "Idle", "Running", "Held"}

# get_progress() returns every Submission created within this window whatever
# its POMS Status, plus any POMS-active one of any age. A fixed constant, not
# a config knob.
SUBMISSION_WINDOW = timedelta(hours=72)
LAUNCH_SOURCE_MAX_WINDOWS = 12


class LaunchFailed(RuntimeError):
    """POMS marked a just-launched Submission LaunchFailed, so it will never
    get a jobsub_job_id (e.g. its Input Dataset was empty)."""


class EmptyDataset(LaunchFailed):
    """The launch failed because the Input Dataset had no files: POMS's launch
    log ends with "Ignoring launch request on empty dataset"."""


# POMS's launch log for such a launch ends with this line, a timestamp and
# "== completed: <pid> ==" (confirmed live, submission 3181392, 2026-11-15).
EMPTY_DATASET_MARKER = "Ignoring launch request on empty dataset"
LAUNCH_LOG_TAIL_LINES = 4
LAUNCH_LOG_SETTLE_TRIES = 6


class PomsSession:
    def __init__(self, pc, cfg):
        self.pc = pc
        self.cfg = cfg
        pc.update_session_experiment(cfg["experiment"])
        pc.update_session_role(cfg["role"])
        self._campaign_stage_id = None
        self._cache = None

    @property
    def campaign_stage_id(self):
        if self._campaign_stage_id is None:
            self._campaign_stage_id = self.pc.get_campaign_stage_id(
                self.cfg["experiment"], self.cfg["campaign_name"], self.cfg["campaign_stage_name"]
            )
        return self._campaign_stage_id

    @property
    def cache_file(self):
        """The state file whose `submissions` section caches jobsub_job_id/subgroup
        per submission_id, or None if cfg has none; see docs/adr/0008-cache-static-submission-fields.md."""
        return self.cfg.get("state_file")

    @property
    def cache(self):
        if self._cache is None:
            self._cache = self._load_cache()
        return self._cache

    def _load_cache(self):
        if not self.cache_file:
            return {}
        cache = state.read(self.cfg, "submissions", {})
        return cache if isinstance(cache, dict) else {}

    def _cache_submission(self, submission_id, jobsub_job_id, subgroup):
        if not self.cache_file:
            return
        self.cache[str(submission_id)] = {"jobsub_job_id": jobsub_job_id, "subgroup": subgroup}
        state.write(self.cfg, "submissions", self.cache)

    def _fetch_submission_details(self, submission_id):
        """submission_details(), caching jobsub_job_id/subgroup on success.
        Returns (ok, details) exactly like pc.submission_details()."""
        ok, details = self.pc.submission_details(self.cfg["experiment"], self.cfg["role"], submission_id)
        if ok:
            submission = details.get("submission", {})
            jobsub_job_id = submission.get("jobsub_job_id")
            if jobsub_job_id:
                subgroup = self._parse_subgroup(submission.get("command_executed"))
                self._cache_submission(submission_id, jobsub_job_id, subgroup)
        return ok, details

    def get_progress(self, now=None):
        """Every Submission in the 72h window, plus any POMS-active one of any
        age. POMS Status is returned as data, never used to filter recent ones."""
        ok, resp = self.pc.campaign_stage_submissions(
            self.cfg["experiment"], self.cfg["role"], self.cfg["campaign_name"], self.cfg["campaign_stage_name"],
        )
        submissions = resp.get("data", {}).get("submissions", []) if ok else []
        if not submissions:
            logging.info(
                "no submissions found yet for %s/%s", self.cfg["campaign_name"], self.cfg["campaign_stage_name"]
            )
            return []

        submissions = sorted(submissions, key=lambda s: s.get("submission_id", 0))
        cutoff = (now or datetime.now()) - SUBMISSION_WINDOW
        target = [s for s in submissions if self._in_window(s, cutoff)]

        result = []
        for s in target:
            submission_id = s.get("submission_id")
            cached = self.cache.get(str(submission_id))
            if cached is not None:
                jobsub_job_id = cached["jobsub_job_id"]
                subgroup = cached["subgroup"]
            else:
                ok, details = self._fetch_submission_details(submission_id)
                submission = details.get("submission", {}) if ok else {}
                jobsub_job_id = submission.get("jobsub_job_id")
                subgroup = self._parse_subgroup(submission.get("command_executed"))
            entry = {
                "submission_id": submission_id,
                "status": s.get("status"),
                "jobsub_job_id": jobsub_job_id,
                "subgroup": subgroup,
            }
            if not self._in_window(s, cutoff, active_counts=False):
                entry["outside_window"] = True
            result.append(entry)

        return result

    @staticmethod
    def _in_window(s, cutoff, active_counts=True):
        if active_counts and s.get("status") in ACTIVE_SUBMISSION_STATUSES:
            return True
        created = s.get("created")
        # Naive Central-time string, same as history[].created.
        return created is None or datetime.fromisoformat(created) >= cutoff

    @staticmethod
    def _parse_subgroup(command_executed):
        match = SUBGROUP_COMMAND_PATTERN.search(command_executed or "")
        return match.group(1) if match else None

    def get_launch_command(self):
        """The jobsub command POMS ran for the newest Submission that is not
        a Test Launch (else the newest of any kind), URL-unquoted. None when
        no Submission has one."""
        source = self.get_launch_source()
        return source["command"] if source else None

    def get_launch_source(self):
        """{command, submission_id, is_test, windows} of the Submission
        get_launch_command() reads, or None when no Submission has one.
        `is_test` is True for the newest-of-any-kind fallback. POMS lists
        one time window per call, so older windows are read, at most
        LAUNCH_SOURCE_MAX_WINDOWS, until a non-test Submission with a command
        is found; `windows` is how many were read."""
        newest_of_any_kind = None
        kwargs = {}
        windows = 0
        while windows < LAUNCH_SOURCE_MAX_WINDOWS:
            ok, resp = self.pc.campaign_stage_submissions(
                self.cfg["experiment"], self.cfg["role"], self.cfg["campaign_name"], self.cfg["campaign_stage_name"],
                **kwargs,
            )
            windows += 1
            data = resp.get("data", {}) if ok else {}
            for s in sorted(data.get("submissions", []), key=lambda s: s.get("submission_id", 0), reverse=True):
                ok, details = self._fetch_submission_details(s.get("submission_id"))
                submission = details.get("submission", {}) if ok else {}
                command = submission.get("command_executed")
                if not command:
                    continue
                found = {"command": unquote(command), "submission_id": s.get("submission_id"), "windows": windows}
                if not (submission.get("submission_params") or {}).get("test"):
                    return {**found, "is_test": False}
                newest_of_any_kind = newest_of_any_kind or {**found, "is_test": True}
            older = self._older_window(data.get("prevlink"))
            if not older or older == kwargs:
                break
            kwargs = older
        if newest_of_any_kind:
            newest_of_any_kind["windows"] = windows
        return newest_of_any_kind

    @staticmethod
    def _older_window(prevlink):
        """The tmax/tdays kwargs of the previous time window, from the
        prevlink of a campaign_stage_submissions response; None without one."""
        query = parse_qs(urlparse(prevlink or "").query)
        if "tmax" not in query:
            return None
        return {key: query[key][0] for key in ("tmax", "tdays") if key in query}

    def get_submission_source(self, submission_id):
        """{command, submission_id, is_test, dataset} of one Submission,
        looked up by id (no campaign or stage needed), or None when POMS has
        no command for it. `dataset` is its stage snapshot's Input Dataset."""
        ok, details = self._fetch_submission_details(submission_id)
        submission = details.get("submission", {}) if ok else {}
        command = submission.get("command_executed")
        if not command:
            return None
        return {
            "command": unquote(command),
            "submission_id": submission_id,
            "is_test": bool((submission.get("submission_params") or {}).get("test")),
            "dataset": (submission.get("campaign_stage_snapshot_obj") or {}).get("dataset"),
        }

    def get_stage_params(self):
        """Read the current params for the target Campaign Stage."""
        ok, resp = self.pc.show_campaign_stages(campaign_name=self.cfg["campaign_name"])
        if not ok:
            raise RuntimeError("show_campaign_stages failed")
        for stage in resp.get("campaign_stages", []):
            if stage.get("name") == self.cfg["campaign_stage_name"]:
                return stage
        raise RuntimeError(f"stage {self.cfg['campaign_stage_name']!r} not found in campaign {self.cfg['campaign_name']!r}")

    def set_subgroup(self, use_pro):
        """Set or clear the pro subgroup override for the Campaign Stage's next submission."""
        updates = {SUBGROUP_OVERRIDE_KEY: PRO_SUBGROUP if use_pro else ""}
        self.update_stage_params(updates)

    def update_stage_params(self, updates):
        """Apply param_overrides updates to the Campaign Stage, if any."""
        if not updates:
            logging.info("no stage param updates to apply")
            return

        logging.info("updating stage params: %s", updates)
        # Must pre-serialize; see docs/poms_client_gotchas.md.
        param_overrides = str(list(updates.items()))
        data = self.pc.update_stage_param_overrides(
            self.cfg["experiment"], self.campaign_stage_id, param_overrides=param_overrides
        )
        if data is None:
            raise RuntimeError(f"update_stage_param_overrides failed for campaign_stage_id={self.campaign_stage_id}")

    def set_recovery_input_dataset(self, dataset_name):
        """Set a new Input Dataset and reset cs_last_split to 0 (recovery.py).
        Confirmed live -- see docs/poms_client_gotchas.md."""
        data, status = raw_poms_call(
            self.pc, "update_campaign_stage",
            pcl_call=1,
            campaign_stage=self.campaign_stage_id,
            experiment=self.cfg["experiment"],
            role=self.cfg["role"],
            dataset=dataset_name,
            cs_last_split=0,
        )
        if status not in (200, 202):
            raise RuntimeError(f"update_campaign_stage failed: HTTP status {status}\n{data}")
        logging.info(
            "set recovery input dataset=%s, cs_last_split=0 for campaign_stage_id=%s",
            dataset_name, self.campaign_stage_id,
        )

    def set_split_type(self, split_type):
        """Set the Campaign Stage's cs_split_type and reset cs_last_split to 0.

        POMS resumes `new()` from cs_last_split (the end of the last window it
        served) and ignores a changed firsttime while it is set; confirmed live
        2026-10-23, see docs/adr/0024."""
        data, status = raw_poms_call(
            self.pc, "update_campaign_stage",
            pcl_call=1,
            campaign_stage=self.campaign_stage_id,
            experiment=self.cfg["experiment"],
            role=self.cfg["role"],
            cs_split_type=split_type,
            cs_last_split=0,
        )
        if status not in (200, 202):
            raise RuntimeError(f"update_campaign_stage failed: HTTP status {status}\n{data}")
        logging.info("set cs_split_type=%s for campaign_stage_id=%s", split_type, self.campaign_stage_id)

    def launch_log(self, submission_id):
        """Return (lines, still_writing) for a Submission's launch stdout, as
        POMS saved it. still_writing is POMS's auto-refresh hint: the file was
        modified in the last 30s."""
        data, status = raw_poms_call(
            self.pc, "list_launch_file",
            campaign_stage_id=self.campaign_stage_id,
            submission_id=submission_id,
            experiment=self.cfg["experiment"],
            role=self.cfg["role"],
            fmt="json",
        )
        if status != 200:
            raise RuntimeError(f"list_launch_file failed: HTTP status {status}\n{data}")
        body = json.loads(data)
        return body["lines"], bool(body["do_refresh"])

    def submit_next_slice(self):
        """Launch a new Submission for the Campaign Stage. Returns the new
        submission_id, or None if POMS reports the campaign stage's Input
        Dataset is exhausted (see docs/poms_client_gotchas.md) -- treat that
        as graceful completion, not a failure.
        """
        # Bypasses pc.make_poms_call() (crashes on success via its wrappers,
        # mangles the error body on failure); see docs/poms_client_gotchas.md.
        data, status = raw_poms_call(
            self.pc, "launch_jobs",
            campaign_stage_id=self.campaign_stage_id,
            experiment=self.cfg["experiment"],
            role=self.cfg["role"],
            test_launch=1 if self.cfg.get("test_launch") else None,
        )
        if status != 303:
            if NO_MORE_SPLITS_MARKER in data:
                logging.info("launch_jobs: no more splits in this campaign stage -- treating as complete")
                return None
            raise RuntimeError(f"launch_jobs failed: HTTP status {status}\n{data}")
        submission_id = parse_qs(urlparse(data).query).get("submission_id", [None])[0]
        logging.info("submitted new slice: submission_id=%s", submission_id)
        logging.info("Getting job id...")
        jobsub_job_id = None
        printed = 0
        while jobsub_job_id is None:
            time.sleep(JOBSUB_ID_POLL_SECONDS)
            jobsub_job_id, status = self._poll_submission(submission_id)
            if self.cfg.get("follow_launch"):
                printed = self._print_new_launch_lines(submission_id, printed)
            if jobsub_job_id is None and status == "LaunchFailed":
                raise self._launch_failure(submission_id)
        logging.info("jobsub_job_id=%s", jobsub_job_id)
        return submission_id

    def _print_new_launch_lines(self, submission_id, printed):
        """Best-effort: print launch stdout lines past `printed`, return the new count."""
        try:
            lines, _ = self.launch_log(submission_id)
        except Exception as e:
            logging.warning("could not read launch log: %s", e)
            return printed
        sys.stdout.write("".join(lines[printed:]))
        sys.stdout.flush()
        return len(lines)

    def _launch_failure(self, submission_id):
        """EmptyDataset if the finished launch log ends with POMS's
        empty-dataset line, else a plain LaunchFailed."""
        failure = LaunchFailed(f"submission {submission_id} is LaunchFailed in POMS (see its launch log)")
        try:
            for _ in range(LAUNCH_LOG_SETTLE_TRIES):
                lines, still_writing = self.launch_log(submission_id)
                if not still_writing:
                    break
                time.sleep(JOBSUB_ID_POLL_SECONDS)
        except Exception as e:
            logging.warning("could not read launch log of %s: %s", submission_id, e)
            return failure
        tail = [line.strip() for line in lines if line.strip()][-LAUNCH_LOG_TAIL_LINES:]
        if EMPTY_DATASET_MARKER in tail:
            return EmptyDataset(f"submission {submission_id} had no input files (POMS: empty dataset)")
        return failure

    def _get_jobsub_job_id(self, submission_id):
        """Best-effort lookup of the grid job id for a just-submitted Submission."""
        return self._poll_submission(submission_id)[0]

    def _poll_submission(self, submission_id):
        """Best-effort (jobsub_job_id, Status name) of a just-submitted
        Submission; either is None when unknown. Routes through
        _fetch_submission_details() so the id is cached immediately. The
        current Status is the last `history` entry, named by `statusmap`."""
        try:
            ok, details = self._fetch_submission_details(submission_id)
        except Exception:
            return None, None
        if not ok:
            return None, None
        history = details.get("history") or []
        status = (details.get("statusmap") or {}).get(str(history[-1].get("status_id"))) if history else None
        return details.get("submission", {}).get("jobsub_job_id"), status
