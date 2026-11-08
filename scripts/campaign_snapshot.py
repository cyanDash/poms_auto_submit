"""One reading of a Campaign Stage's Submissions and their condor_q progress,
taken once per run, that answers every question about what is in flight; see
docs/adr/0021-campaign-snapshot-answers-in-flight-questions-once-per-run.md.
It only reads: submitting, condor_rm, recovery and cleanup stay with the callers."""

import logging

import condor_progress
import recovery
import state
from poms_session import ACTIVE_SUBMISSION_STATUSES, PRO_SUBGROUP

# Only role sbndpro's managed token can auth as; see docs/adr/0004.
PRO_ELIGIBLE_ROLE = "production"

# Consecutive runs a POMS-active Submission may sit in one Status with no
# condor_q data before a manual-intervention WARNING is logged every run.
STUCK_NO_DATA_RUNS_WARN = 5

# Per-Submission gate for cleanup_ready(); see docs/adr/0016-cleanup-gates-on-last-slice-completion.md.
CLEANUP_PCT_COMPLETE_THRESHOLD = 98


def no_splits_left(cfg):
    return cfg["max_splits"] - cfg["last_split"] <= 0


class CampaignSnapshot:
    def __init__(self, cfg, submissions, get_condor_progress=None):
        self._cfg = cfg
        self._submissions = list(submissions)
        self._get_condor_progress = get_condor_progress or condor_progress.get_progress
        self._observed = None

    @classmethod
    def take(cls, cfg, session, get_condor_progress=None):
        return cls(cfg, session.get_progress(), get_condor_progress)

    def entries(self):
        """(Submission, Progress) pairs; stops at the first condor_q error."""
        return list(self._observe()[0])

    @property
    def condor_error(self):
        return self._observe()[1]

    def slices_to_plan(self):
        """One entry per new slice to submit this run (True = pro, False =
        standard), possibly empty."""
        cfg = self._cfg
        if no_splits_left(cfg):
            logging.info(
                "decision: skip (max_splits reached: last_split=%d max_splits=%d)",
                cfg["last_split"], cfg["max_splits"],
            )
            if cfg["do_recovery"] and not recovery.is_handled(cfg):
                logging.info("recovery: not done (max_splits reached before the splits were exhausted)")
            return []

        remaining_splits = cfg["max_splits"] - cfg["last_split"]
        target = 2 if cfg["submit_two_slices"] else 1
        in_flight = self._in_flight()
        num_slices = min(max(0, target - len(in_flight)), remaining_splits)
        subgroup_plan = _plan_subgroups(num_slices, cfg["role"], _pro_available(in_flight))
        logging.info(
            "decision: submit %d slice(s) (in_flight=%d target=%d remaining_splits=%d) subgroup=%s",
            num_slices, len(in_flight), target, remaining_splits,
            ["pro" if use_pro else "standard" for use_pro in subgroup_plan],
        )
        return subgroup_plan

    def any_still_running(self, threshold=float("inf")):
        """Whether any Submission is still running, judged by condor_q on
        every one. A live DAG below `threshold`, a POMS-active Submission with
        no data, or a condor_q error all count as running. The default
        threshold makes a live DAG at any percent count."""
        return bool(self._in_flight(threshold))

    def cleanup_ready(self):
        """Whether the campaign is done enough to safely run duplicate-cleanup
        and turn the campaign stage off; see
        docs/adr/0016-cleanup-gates-on-last-slice-completion.md."""
        cfg = self._cfg
        if not (cfg["do_cleanup"] and no_splits_left(cfg)):
            return False
        if cfg["do_recovery"] and not recovery.is_handled(cfg):
            return False
        if not self._submissions:
            return False
        return not self.any_still_running(CLEANUP_PCT_COMPLETE_THRESHOLD)

    def recovery_ready(self):
        """Whether an exhausted campaign stage should be evaluated for recovery
        now: opted in, not yet handled, and nothing still running per condor_q.
        See docs/adr/0019-do-recovery-config-flag-and-internal-handled-marker.md."""
        cfg = self._cfg
        if not cfg["do_recovery"]:
            logging.info("recovery: disabled (do_recovery=0 in config)")
            return False
        if recovery.is_handled(cfg):
            logging.info(
                "recovery: already handled (set recovery_handled to false in %s to re-evaluate)",
                cfg.get("state_file"),
            )
            return False
        if not self._submissions:
            logging.info("recovery: no submission history yet -- waiting")
            return False
        if self.any_still_running():
            logging.info("recovery: a submission is still running per condor_q -- waiting")
            return False
        return True

    def record_stuck(self):
        """Count consecutive runs each POMS-active Submission has sat in the
        same Status with no condor_q data; WARN from STUCK_NO_DATA_RUNS_WARN
        on. Never frees the slot. Anything not stuck is dropped, which resets
        it. Skipped when no splits are left or condor_q errored."""
        cfg = self._cfg
        if no_splits_left(cfg) or not cfg.get("state_file"):
            return
        entries, error = self._observe()
        if error:
            return
        stuck = [
            s for s, progress in entries
            if progress.outcome == "no_data" and s.get("status") in ACTIVE_SUBMISSION_STATUSES
        ]
        try:
            previous = state.read(cfg, "stuck_no_data", {})
            previous = {sid: e for sid, e in previous.items() if isinstance(e, dict) and isinstance(e.get("count"), int)}
        except AttributeError:
            previous = {}

        current = {}
        for s in stuck:
            submission_id = str(s.get("submission_id"))
            prior = previous.get(submission_id)
            count = prior["count"] + 1 if prior and prior["status"] == s.get("status") else 1
            current[submission_id] = {"status": s.get("status"), "count": count}
            if count >= STUCK_NO_DATA_RUNS_WARN:
                logging.warning(
                    "submission_id=%s has been %s with no condor_q data for %d consecutive runs "
                    "-- manual intervention needed",
                    submission_id, s.get("status"), count,
                )
        state.write(cfg, "stuck_no_data", current)

    def _observe(self):
        if self._observed is None:
            entries = []
            error = False
            for s in self._submissions:
                jobsub_job_id = s.get("jobsub_job_id")
                if jobsub_job_id:
                    progress = self._get_condor_progress(self._cfg["experiment"], jobsub_job_id)
                else:
                    progress = condor_progress.Progress("no_data")
                if progress.outcome == "error":
                    logging.warning(
                        "condor_q failed for submission_id=%s -- holding every submission in the window",
                        s.get("submission_id"),
                    )
                    error = True
                    break
                entries.append((s, progress))
                _log_live(s, progress)
            self._observed = (entries, error)
        return self._observed

    def _in_flight(self, threshold=None):
        """Submissions still holding a slot, decided from condor_q; POMS Status
        is only the tiebreak when condor_q has no data. See
        docs/adr/0005-in-flight-slot-based-decision.md."""
        threshold = self._cfg["pct_complete_threshold"] if threshold is None else threshold
        entries, error = self._observe()
        if error:
            return list(self._submissions)
        return [s for s, progress in entries if _holds_slot(s, progress, threshold)]


def _holds_slot(s, progress, threshold):
    if progress.outcome == "live":
        return progress.pct < threshold
    if progress.outcome == "finished":
        return False
    return s.get("status") in ACTIVE_SUBMISSION_STATUSES


def _log_live(s, progress):
    """Only Submissions condor_q reports as live are logged; POMS Status is
    deliberately left out, see docs/adr/0017."""
    if progress.outcome != "live":
        return
    logging.info(
        "progress: submission_id=%s status=%s completion_%%=%.2f jobsub_job_id=%s subgroup=%s",
        s.get("submission_id"), progress.job_status, progress.pct, s.get("jobsub_job_id"), s.get("subgroup"),
    )


def _pro_available(in_flight):
    """Whether the campaign's single pro slot is free; see CONTEXT.md's
    Subgroup entry."""
    return not any(s.get("subgroup") == PRO_SUBGROUP for s in in_flight)


def _plan_subgroups(num_slices, role, pro_available):
    """Decide which subgroup each new submission gets; see
    docs/adr/0002-lone-slice-defaults-to-pro-subgroup.md."""
    if num_slices == 0:
        return []
    if role != PRO_ELIGIBLE_ROLE or not pro_available:
        return [False] * num_slices
    return [True] + [False] * (num_slices - 1)
