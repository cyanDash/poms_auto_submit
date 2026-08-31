"""PomsSession: the seam to poms_client. Owns identity setup, campaign_stage_id
resolution, and normalizes poms_client's response shapes behind plain method
returns -- callers never see a raw (ok, data) tuple, bare string, or redirect
URL.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

SUBGROUP_OVERRIDE_KEY = "-Osubmit.subgroup="
PRO_SUBGROUP = "pro"

# statuses[] entries are [label, count, dims_url] triples -- see
# docs/poms_client_gotchas.md.
STATUS_LABEL_SUBMITTED = "Submitted to SAM: "
STATUS_LABEL_PENDING = "Pending: "

# The stage's current param_overrides (what SUBGROUP_OVERRIDE_KEY writes to)
# gets overwritten by later runs and doesn't reflect what a past submission
# actually launched with -- and for a Test Launch it's not even consulted,
# since POMS substitutes test_param_overrides server-side instead (see
# docs/poms_client_gotchas.md). command_executed is the one per-submission,
# immutable record of the --subgroup= flag POMS actually launched jobs with.
SUBGROUP_COMMAND_PATTERN = re.compile(r"--subgroup=(\S+)")

# POMS doesn't assign a Submission's jobsub_job_id synchronously with
# launch_jobs -- observed live 2026-08-26 as still None ~immediately after a
# successful submit. Poll for it instead of a single fixed wait.
JOBSUB_ID_POLL_SECONDS = 5

# A Submission flips from Running to Held as soon as any of its jobs get held
# (e.g. asked for more grid resources than allowed) -- even if only ~5% of a
# 10k-job Submission is held and the rest are still running fine. Treated as
# still active/in-flight, not as done or ignorable. New and Idle are included
# too: neither has started progressing yet, but both are still in-flight, not
# abandoned.
ACTIVE_SUBMISSION_STATUSES = {"New", "Idle", "Running", "Held"}


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
        """Where jobsub_job_id/subgroup get cached per submission_id, or None
        if cfg has no cache_dir (caching becomes a no-op -- see
        docs/adr/0008-cache-static-submission-fields.md)."""
        cache_dir = self.cfg.get("cache_dir")
        if not cache_dir:
            return None
        return os.path.join(cache_dir, f"{self.campaign_stage_id}.json")

    @property
    def cache(self):
        if self._cache is None:
            self._cache = self._load_cache()
        return self._cache

    def _load_cache(self):
        cache_file = self.cache_file
        if not cache_file:
            return {}
        try:
            with open(cache_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _cache_submission(self, submission_id, jobsub_job_id, subgroup):
        cache_file = self.cache_file
        if not cache_file:
            return
        self.cache[str(submission_id)] = {"jobsub_job_id": jobsub_job_id, "subgroup": subgroup}
        with open(cache_file, "w") as f:
            json.dump(self.cache, f, indent=2)

    def _fetch_submission_details(self, submission_id):
        """submission_details(), caching the static jobsub_job_id/subgroup
        fields on success. Returns (ok, details) exactly like
        pc.submission_details() -- callers still read the dynamic fields
        (pct_complete, history, statuses) straight out of details."""
        ok, details = self.pc.submission_details(self.cfg["experiment"], self.cfg["role"], submission_id)
        if ok:
            submission = details.get("submission", {})
            jobsub_job_id = submission.get("jobsub_job_id")
            if jobsub_job_id:
                subgroup = self._parse_subgroup(submission.get("command_executed"))
                self._cache_submission(submission_id, jobsub_job_id, subgroup)
        return ok, details

    def get_progress(self):
        """Status/pct_complete of the currently relevant Submission(s)."""
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
        active = [s for s in submissions if s.get("status") in ACTIVE_SUBMISSION_STATUSES]
        target = active if active else [submissions[-1]]

        result = []
        for s in target:
            submission_id = s.get("submission_id")
            cached = self.cache.get(str(submission_id))
            if cached is not None:
                pct_complete = last_status_change = files_submitted = files_pending = None
                jobsub_job_id = cached["jobsub_job_id"]
                subgroup = cached["subgroup"]
            else:
                ok, details = self._fetch_submission_details(submission_id)
                submission = details.get("submission", {}) if ok else {}
                pct_complete = submission.get("pct_complete")
                jobsub_job_id = submission.get("jobsub_job_id")
                subgroup = self._parse_subgroup(submission.get("command_executed"))
                statuses = details.get("statuses", []) if ok else []
                last_status_change = self._last_status_change(details.get("history", []) if ok else [])
                files_submitted = self._status_count(statuses, STATUS_LABEL_SUBMITTED)
                files_pending = self._status_count(statuses, STATUS_LABEL_PENDING)
            entry = {
                "submission_id": submission_id,
                "status": s.get("status"),
                "pct_complete": pct_complete,
                "jobsub_job_id": jobsub_job_id,
                "subgroup": subgroup,
                "last_status_change": last_status_change,
                "files_submitted": files_submitted,
                "files_pending": files_pending,
            }
            result.append(entry)

        return result

    @staticmethod
    def _parse_subgroup(command_executed):
        match = SUBGROUP_COMMAND_PATTERN.search(command_executed or "")
        return match.group(1) if match else None

    @staticmethod
    def _last_status_change(history):
        """Most recent history[].created timestamp, or None if there's no
        history yet. Naive Central-time strings -- see
        docs/poms_client_gotchas.md."""
        created = [entry.get("created") for entry in history if entry.get("created")]
        if not created:
            return None
        return max(datetime.fromisoformat(c) for c in created)

    @staticmethod
    def _status_count(statuses, label):
        for entry_label, count, *_ in statuses:
            if entry_label == label:
                return count
        return None

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
        # requests' form-encoder flattens a dict value to just its keys, dropping
        # the values -- must pre-serialize (see docs/poms_client_gotchas.md)
        param_overrides = str(list(updates.items()))
        data = self.pc.update_stage_param_overrides(
            self.cfg["experiment"], self.campaign_stage_id, param_overrides=param_overrides
        )
        if data is None:
            raise RuntimeError(f"update_stage_param_overrides failed for campaign_stage_id={self.campaign_stage_id}")

    def submit_next_slice(self):
        """Launch a new Submission for the Campaign Stage."""
        # launch_campaign_stage_jobs() wraps this but crashes on success (see
        # docs/poms_client_gotchas.md) -- call make_poms_call directly instead
        data, status = self.pc.make_poms_call(
            method="launch_jobs",
            campaign_stage_id=self.campaign_stage_id,
            experiment=self.cfg["experiment"],
            role=self.cfg["role"],
            test_launch=1 if self.cfg.get("test_launch") else None,
        )
        if status != 303:
            raise RuntimeError(f"launch_jobs failed: status={status} data={data}")
        submission_id = parse_qs(urlparse(data).query).get("submission_id", [None])[0]
        logging.info("submitted new slice: submission_id=%s", submission_id)
        logging.info("Getting job id...")
        jobsub_job_id = None
        while jobsub_job_id is None:
            time.sleep(JOBSUB_ID_POLL_SECONDS)
            jobsub_job_id = self._get_jobsub_job_id(submission_id)
        logging.info("jobsub_job_id=%s", jobsub_job_id)
        return submission_id

    def _get_jobsub_job_id(self, submission_id):
        """Best-effort lookup of the grid job id for a just-submitted Submission.

        Routes through _fetch_submission_details() so a freshly-launched
        submission's jobsub_job_id/subgroup land in the cache immediately --
        get_progress() never has to pay for them again on a later run.
        """
        try:
            ok, details = self._fetch_submission_details(submission_id)
        except Exception:
            return None
        if not ok:
            return None
        return details.get("submission", {}).get("jobsub_job_id")
