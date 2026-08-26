"""PomsSession: the seam to poms_client. Owns identity setup, campaign_stage_id
resolution, and normalizes poms_client's response shapes behind plain method
returns -- callers never see a raw (ok, data) tuple, bare string, or redirect
URL.
"""

import logging
import types
from urllib.parse import parse_qs, urlparse

SUBGROUP_OVERRIDE_KEY = "-Osubmit.subgroup="
PRO_SUBGROUP = "pro"

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

    @property
    def campaign_stage_id(self):
        if self._campaign_stage_id is None:
            self._campaign_stage_id = self.pc.get_campaign_stage_id(
                self.cfg["experiment"], self.cfg["campaign_name"], self.cfg["campaign_stage_name"]
            )
        return self._campaign_stage_id

    def check_auth(self):
        """Best-effort warning if the proxy/token uploaded to POMS looks stale."""
        options = types.SimpleNamespace(
            test=None, experiment=self.cfg["experiment"], verbose=False
        )
        # suppress poms_client's noisy traceback-on-failure inside check_stale_*()
        root_logger = logging.getLogger()
        previous_level = root_logger.level
        root_logger.setLevel(logging.CRITICAL)
        try:
            if self.pc.auth_token():
                stale = self.pc.check_stale_token(options)
            else:
                stale = self.pc.check_stale_proxy(options)
        except Exception:
            root_logger.setLevel(previous_level)
            logging.exception("could not check auth staleness, continuing anyway")
            return
        root_logger.setLevel(previous_level)
        if stale:
            logging.warning("POMS auth (proxy/token) looks stale — renew before relying on this run")

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
            ok, details = self.pc.submission_details(self.cfg["experiment"], self.cfg["role"], submission_id)
            submission = details.get("submission", {}) if ok else {}
            pct_complete = submission.get("pct_complete")
            jobsub_job_id = submission.get("jobsub_job_id")
            entry = {
                "submission_id": submission_id,
                "status": s.get("status"),
                "pct_complete": pct_complete,
                "jobsub_job_id": jobsub_job_id,
            }
            logging.info(
                "progress: campaign_stage_id=%s submission_id=%s status=%s pct_complete=%s jobsub_job_id=%s",
                self.campaign_stage_id, submission_id, entry["status"], pct_complete, jobsub_job_id,
            )
            result.append(entry)

        return result

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
        logging.info("update_stage_param_overrides response: %s", data)

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
        return submission_id
