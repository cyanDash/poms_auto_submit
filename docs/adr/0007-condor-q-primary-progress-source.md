# Make condor_q the primary progress source, POMS signals a fallback chain

## The problem

`docs/adr/0005-in-flight-slot-based-decision.md` made "in-flight" (active
*and* under `pct_complete_threshold`) the shared concept
`next_slice_count()` and `pro_available()` both key off of. That took on an
unguarded risk: nothing stops `pct_complete` from simply not moving. Two
live incidents confirmed it, on different failure modes:

1. **Submission 3136636** (2026-08-30): `pct_complete` stuck at `0.043` for
   two-plus days while `files_consumed=9982/10000` and the `statuses` array
   (`Submitted to SAM: 10000`, `Pending: 299`) — both computed fresh
   server-side per call, unlike the cached `pct_complete` field — show
   ~97-99% real progress. `history` confirms it's `pct_complete` itself
   that's stuck, not the underlying work: repeated `status_id=4000`
   ("Running") entries between `09:03` and `11:37` on 2026-08-28, then
   nothing for two days.
2. **Submission 3138475** (2026-08-30): a *second* signal can independently
   fail too. Its `statuses` array is all zeros because the dims strings
   reference `snapshot_for_project_name None` — the submission never got
   attached to a real SAM project while stuck in `New` (`history` has one
   entry from `10:47`, 11 hours before it was checked). A manual
   `condor_q -G sbnd -J 29756425@jobsub04.fnal.gov` lookup showed the real
   state: `TOTAL=10002`, `DONE=1085`, `RUN=465`, `IDLE=8438` — genuinely
   active and progressing, just invisible to *either* POMS-side signal.

## The fix: condor_q primary, POMS signals as fallback

HTCondor itself is the actual ground truth for job progress, and it worked
in incident 2 precisely when both POMS-side signals independently failed.
So `condor_progress.get_pct_complete()` is queried on **every run, for
every active submission with a `jobsub_job_id`** — not held in reserve for
a rare stuck case — and its result wins whenever it's available. Three
layers, in order, per submission:

1. **condor_q** (`condor_progress.get_pct_complete()`): `DAG_NodesDone /
   DAG_NodesTotal * 100` for the submission's DAGMan controller job.
2. **stale-status statuses-array proxy** (`_stale_status_proxy_pct_complete()`
   in `poms_auto_submit.py`, only reached when layer 1 returns `None`): if
   `last_status_change` (max `history[].created`, parsed by
   `PomsSession.get_progress()`) is more than `STALE_STATUS_HOURS=2` old,
   compute `(files_submitted - files_pending) / files_submitted * 100` from
   the `statuses` array — this is what incident 1's original fix looked
   like before this ADR demoted it to a fallback layer. `Pending` is
   defined server-side as "submitted minus has-a-produced-output-child,"
   the same notion `campaign_stage_obj.completion_type`/`completion_pct`
   already uses to judge a stage "complete."
3. **raw `pct_complete`**: used when neither of the above produced a value
   (e.g. not stale yet, or no usable file counts either).

`STALE_STATUS_HOURS=2` is hard-coded, not a config knob — roughly two
missed hourly cron cycles, deliberately small since it's now only a
fallback for when `condor_q` itself is down, not the primary defense.

## Why the query targets the bare DAGMan controller job, not `-J`

`condor_q`'s `-J <jobsub_job_id>` convenience flag expands to **every
individual child job** under that submission — confirmed live: a query
against a 10k-job submission returned thousands of rows, each with
`DAG_NodesDone`/`DAG_NodesTotal` reading `undefined`. Those attributes live
only on the DAGMan controller job's own ClassAd, not on its workers — and
completed child jobs have already left the schedd's queue entirely, so
per-child enumeration under `-J` can't yield a `DONE` count either way.

The fix: extract the bare cluster id from `jobsub_job_id`
(`"<cluster>@<schedd>"` → `<cluster>`, the DAGMan controller job's own
cluster id) and query *that job directly* — confirmed live:

```
condor_q -G sbnd 29756425 -autoformat:h JobStatus DAG_NodesDone DAG_NodesTotal
```

returns one structured line per matching job:
`JobStatus=2 DAG_NodesDone=1177 DAG_NodesTotal=10002`. This is a
`man condor_q`-documented pass-through of real HTCondor's `-autoformat`/
`-af` flag: `condor_q -help` here initially looked like jobsub_lite's
wrapper only exposed a small custom flag set with no structured-output
option, but `man condor_q` documents "also condor_q arguments" — the
wrapper passes the full real-condor_q flag set through underneath.

## Parsing quirk: repeated header lines

`-autoformat:h` printed the header line (the `ATTRS` names, space-joined)
**repeated multiple times**, with the one real data line mixed in among the
repeats (observed live, not a fixed header-then-data layout). Since the
header line's first token is the literal string `"JobStatus"` and the data
line's first token is always a small integer, `_parse_data_row()` scans all
lines for one that splits into exactly `len(ATTRS)` tokens whose first
token is all-digits, rather than assuming a fixed line position.

## Other quirks

- The `-G`/`--group` flag `get_pct_complete()` relies on is jobsub_lite's
  `condor_q` wrapper, not real HTCondor's `condor_q` — the latter rejects it
  outright (`Error: unrecognized argument -G`, exit 1). An interactive login
  shell's `$PATH` puts `/opt/jobsub_lite/bin` ahead of `/usr/bin`, so this
  went unnoticed in dev; cron's minimal `$PATH` doesn't include it and
  silently resolved `condor_q` to the plain HTCondor binary instead (observed
  live 2026-08-31, first cron run after this ADR shipped). Fixed by calling
  the wrapper via its fixed absolute path (`condor_progress.CONDOR_Q_BIN`)
  rather than trusting `$PATH` — that path is FNAL-wide, not
  sandbox-specific: it's the same one POMS records in `command_executed` for
  every `jobsub_submit` call.
- This wrapper's tracing module throws a `KeyError` on
  `OTEL_EXPORTER_JAEGER_ENDPOINT` and prints a traceback to stderr on
  *every* invocation (observed in this dev sandbox), but still exits `0`
  ("Continuing without tracing..."). `get_pct_complete()` judges failure by
  `returncode`/output shape, never by stderr being non-empty.
- `DAG_NodesDone`/`DAG_NodesTotal` read as the literal string `"undefined"`
  on a non-DAGMan job, or a DAGMan job HTCondor hasn't started tracking
  yet — treated as "no data," falling through to layer 2/3, not as `0`.

## Accepted operational tradeoff

`condor_q`/jobsub_lite must now be available and authenticated on *every*
cron run, not just the rare stuck case — a `condor_q` outage now affects
every decision (falling through to layer 2/3, not failing the run: see
`_effective_pct_complete()`), where before this ADR it would have had no
effect at all. Judged worth it: incident 2 showed the POMS-only fallback
chain alone isn't reliable enough on its own.

## Accepted risk

A submission that's stale for `STALE_STATUS_HOURS` (layer 2) but whose
`files_submitted`/`files_pending` themselves haven't been refreshed
recently either (e.g. a broader POMS/SAM outage, not just `pct_complete`)
could compute a proxy off equally-stale data. Not guarded against — layer 2
only exists as a second-line fallback for when `condor_q` is briefly
unavailable, not as the primary defense this ADR is about.
