"""Track Stragglers and condor_rm the ones that stall, so recovery starts with
nothing running. See docs/adr/0018-remove-stalled-stragglers-before-recovery.md."""

import json
import logging
import os
from datetime import datetime

import condor_progress
import condor_rm

# A live DAG with this many unfinished jobs or fewer becomes a Straggler.
STRAGGLER_MAX_UNFINISHED = 50
# Unfinished must fall by this much below the expected value to count as progress.
STAGGER_DROP = 5
# Consecutive stalled runs before the Submission is removed.
STAGGER_LIMIT = 3


def _cache_file(cfg, campaign_stage_id):
    cache_dir = cfg.get("cache_dir")
    if not cache_dir or campaign_stage_id is None:
        return None
    return os.path.join(cache_dir, f"stragglers_{campaign_stage_id}.json")


def _load(cache_file):
    try:
        with open(cache_file) as f:
            records = json.load(f)
        return {
            sid: r for sid, r in records.items()
            if isinstance(r, dict) and all(isinstance(r.get(k), int) for k in ("current_unfinished", "expected_unfinished", "stagger_count"))
        }
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}


def manage(cfg, campaign_stage_id, submissions, get_progress=None, remove=None, dry_run=False, now=None):
    """One run's Straggler pass over the Submissions already examined. Only
    call once no splits are left. A condor_q error changes no records; a
    dry run logs what it would remove and writes nothing."""
    cache_file = _cache_file(cfg, campaign_stage_id)
    if not cache_file:
        return
    get_progress = get_progress or condor_progress.get_progress
    remove = remove or condor_rm.remove
    now = (now or datetime.now()).isoformat(timespec="seconds")
    previous = _load(cache_file)

    progress_by_id = {}
    for s in submissions:
        progress = get_progress(cfg["experiment"], s["jobsub_job_id"]) if s.get("jobsub_job_id") else condor_progress.Progress("no_data")
        if progress.outcome == "error":
            logging.warning("condor_q failed for submission_id=%s -- straggler records unchanged", s.get("submission_id"))
            return
        progress_by_id[str(s.get("submission_id"))] = (s, progress)

    current = {}
    for sid, (s, progress) in progress_by_id.items():
        record = previous.get(sid)
        if progress.outcome != "live" or progress.unfinished is None:
            continue
        if record is None and progress.unfinished > STRAGGLER_MAX_UNFINISHED:
            continue
        if record is None and s.get("outside_window"):
            logging.warning(
                "submission_id=%s is a Straggler (unfinished=%d) outside the 72h window "
                "-- not tracked, remove it manually if it is stuck", sid, progress.unfinished,
            )
            continue

        record = _advance(record, progress.unfinished, now)
        if record["stagger_count"] >= STAGGER_LIMIT:
            if _remove(cfg, s, record, remove, dry_run):
                continue
        current[sid] = record

    if not dry_run:
        with open(cache_file, "w") as f:
            json.dump(current, f, indent=2)


def _advance(record, unfinished, now):
    if record is None:
        return {
            "current_unfinished": unfinished, "expected_unfinished": unfinished - STAGGER_DROP,
            "stagger_count": 0, "first_tracked": now, "updated": now,
        }
    record = dict(record)
    record["current_unfinished"] = unfinished
    record["updated"] = now
    if unfinished <= record["expected_unfinished"]:
        record["stagger_count"] = 0
        record["expected_unfinished"] = unfinished - STAGGER_DROP
    else:
        record["stagger_count"] += 1
    return record


def _remove(cfg, s, record, remove, dry_run):
    """Returns True when the record can be dropped (removed, or only pretended to)."""
    sid, jobsub_job_id = s.get("submission_id"), s.get("jobsub_job_id")
    detail = "unfinished=%d stagger_count=%d first_tracked=%s" % (
        record["current_unfinished"], record["stagger_count"], record.get("first_tracked"),
    )
    if dry_run:
        logging.warning("dry-run: would condor_rm submission_id=%s jobsub_job_id=%s %s", sid, jobsub_job_id, detail)
        return False
    if remove(cfg["experiment"], jobsub_job_id):
        logging.warning("condor_rm removed stalled Straggler submission_id=%s jobsub_job_id=%s %s", sid, jobsub_job_id, detail)
        return True
    logging.warning("condor_rm FAILED for stalled Straggler submission_id=%s jobsub_job_id=%s %s -- will retry", sid, jobsub_job_id, detail)
    return False
