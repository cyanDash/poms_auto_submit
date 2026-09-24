# POMS Status is advisory; condor_q gates what is in flight

After the Fermilab power outage POMS stopped reporting Submission state
reliably, in both directions. `condor_q` (HTCondor) is now the only evidence of
whether a Submission is still running. POMS Status and `pct_complete` are
advisory at most.

## The incident

ICARUS fake-data Campaign Stage, 2026-09-17 to 2026-09-18. POMS was wrong
three ways at once:

- **False `Completed` / `Located`.** POMS marked Submissions `Completed` about
  21 minutes and `Located` about 35 minutes after launch, though the jobs take
  hours. One Slice showed `Located` while HTCondor showed 1 of 502 DAG nodes
  done.
- **False `Failed`.** Still-running Submissions were marked `Failed`.
- **False 100 percent.** Running Submissions reported `pct_complete=100.0`.

The script only examined Submissions POMS called active (`New`, `Idle`,
`Running`, `Held`; ADR-0013), so each hourly run saw one Submission in flight
against a target of 2 and launched another Slice. Two bursts followed: Slices 3
to 8, then Slices 9 to 23 (about 15 Slices in 13 hours). All 24 Slices were
submitted exactly once, so the harm was concurrency, not duplication.

### Timeline and accepted side effects

- **9/17:** the burst was noticed and the cron was stopped by hand.
- **9/18:** the cron was restarted, and the second burst (Slices 9 to 23)
  followed.
- **Part-1 slice 4 was submitted twice.** Accepted, not prevented: duplicate
  cleanup (ADR-0016) removes the extra output.
- The 9/17 05:00 to 10:00 hourly burst (Slices 3 to 8) already carries the
  POMS fingerprint: Slice 3 (3153766) was marked `Failed` while running (see
  Raw evidence). Slices 4 to 7 were not pulled.

### Why a burst was harmless here but is not in general

This campaign launches 500 jobs per Submission, so ~15 concurrent Submissions
were absorbed by the scheduler. At 10k jobs per Submission the same behavior
would overwhelm it. The fix therefore does not depend on this campaign's size.

## Decision

`condor_q` is the source of truth. Each run examines every Submission from the
last 72 hours (`SUBMISSION_WINDOW`, a constant, regardless of POMS Status) plus
any POMS-active Submission of any age, and asks `condor_q` about its DAG:

| `condor_q` result | Holds a slot? |
|---|---|
| live DAG below `pct_complete_threshold` | yes, whatever POMS says (`Located`, `Failed`, ...) |
| finished DAG (`JobStatus=4`, or nodes done == total) | no, for good |
| no data | POMS Status is the tiebreak: active holds, terminal frees |
| error / timeout | hold: every Submission in the window counts as in flight |

Holding on error is deliberate: a scheduler outage must not trigger a burst.
The log lists only Submissions `condor_q` reports as live (`submission_id`,
`status` as `condor_q` reports it for the DAG, `completion_%`, `jobsub_job_id`, `subgroup`). POMS Status is never logged, and there is
no per-Submission in-flight flag or POMS-disagreement warning.

### Stuck without data

A POMS-active Submission with no `condor_q` data (`Held` is the observed case)
keeps its slot. A count of consecutive runs in the same Status is kept in
`<cache_dir>/stuck_no_data_<campaign_stage_id>.json` (`{submission_id: {status,
count}}`, a no-op without `cache_dir`). From 5 runs (`STUCK_NO_DATA_RUNS_WARN`)
a WARNING says "manual intervention needed" every run. The count resets on a
Status change, on `condor_q` data, or on leaving the active set; a `condor_q`
error leaves it untouched. The warning is information only and never frees the
slot.

### Recovery and cleanup gates

Recovery and duplicate cleanup use the same signal across **all** Submissions in
the window, not just the last one, because a `pro` Slice submitted after a
`standard` one can finish first. They proceed only when every Submission is
finished, or gone with a terminal POMS Status. A live DAG (at any pct), a
POMS-active Submission with no data, or a `condor_q` error means wait. Cleanup
keeps its 98% per-Submission threshold. A false POMS `Failed` no longer sets
`recovery_handled` or flags "needs manual review", so a bad report cannot
permanently disable recovery. `recovery_handled=1` stays the opt-out switch.
Removing stalled Stragglers so this gate can open is ADR-0018.

## What this supersedes

- **ADR-0007:** the fallback layers are gone (stale-status `statuses` proxy,
  `STALE_STATUS_HOURS`, raw `pct_complete`). `condor_q` is queried for every
  Submission in the window. ADR-0007's incident evidence, the bare-DAGMan-job
  query and the parsing quirks still stand.
- **ADR-0013:** the "gate on active POMS Status" rule is gone. Status is now
  only the tiebreak when `condor_q` has no data. Its finding that a terminal
  Submission with no data must not hold a slot forever is preserved by that
  tiebreak.

`pct_complete`, `last_status_change`, `files_submitted` and `files_pending` are
no longer plumbed out of `PomsSession.get_progress()`.

## Still trusted from POMS

`campaign_stage_submissions()` (submission id, creation time, `jobsub_job_id`,
cluster, schedd), `submission_details()` for the immutable subgroup (ADR-0008),
campaign stage id lookup, `show_campaign_stages()`, session identity, and the
write calls (`launch_jobs`, stage param updates). No live-DAG check is added
after launch: during the outage launches produced a jobid and HTCondor ran them
despite POMS misreporting.

## Not in scope

- POMS cannot auto-create Slices for the unsliced part-1 dataset because its
  experiment `sbn` is not registered in POMS's organisation list. The POMS team
  is fixing it; nothing here works around it.
- Fixing POMS's own tracking, or any new config knob.

## Raw evidence

Captured read-only on 2026-09-20 (full responses in `docs/raw/`):
`campaign_stage_submissions_26985.json` (the 79-row list),
`submission_details_{3155064,3155093,3153766}.json`, and `condor_q_2026-09-20.txt`.
All three Submissions report `pct_complete=100.0`. Histories use the statusmap
ids `7000` Completed, `8000` Located, `6000` Failed.

| Submission (launched) | POMS history | `condor_q` at 2026-09-20 17:24Z |
|---|---|---|
| 3155064 (9/18 20:00) | Completed +22 min, Located +35 min; Completed and Located again on 9/20 10:14 and 10:35 | `JobStatus=2`, 11/502 done, 0 failed: still running |
| 3155093 (9/18 21:00) | Completed +22 min, Located +35 min; **Failed** on 9/20 08:57 | `JobStatus=2`, 11/502 done, 0 failed: still running |
| 3153766 (9/17 05:00, Slice 3) | **Failed** +25 min; Completed and Located only on 9/18 10:13 and 10:35 | `JobStatus=4`, 502/502 done: finished |

So POMS reported `pct_complete=100.0` on Submissions with 11 of 502 nodes done,
`Located` while running, `Failed` while running (3155093, and Slice 3 early on),
and it flips Status again long after launch. Slice 3 shows the same signature
as the 9/18 burst, so the 9/17 burst was already affected. `condor_q` was right
in all three cases. Slices 4 to 7 were not pulled.
