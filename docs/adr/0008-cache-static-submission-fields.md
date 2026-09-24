# Cache each submission's static jobsub_job_id/subgroup locally

## The problem

`PomsSession.get_progress()` calls `submission_details()` once per active
submission on *every* hourly cron run, unconditionally. Live-timed against
stage 26971 (`decode_reco1_reco2_caf`, 2026-08-31): `campaign_stage_submissions()`
(the list call, always needed) took ~98-100s; a single `submission_details()`
call took ~48s (raw captures: `docs/raw/campaign_stage_submissions_26971.json`,
`docs/raw/submission_details_3136636_timing.json`). With 6 active submissions
that's roughly `100s + 6×48s ≈ 388s` of POMS calls per run.

Only two of the fields `submission_details()` returns are actually static
per submission: `jobsub_job_id` and `subgroup` (parsed from
`command_executed`, see `docs/poms_client_gotchas.md`'s "Where subgroup
actually lives" — both are fixed the moment a submission is launched and
never change again. The rest of what it returns —
`pct_complete`/`history`/`statuses` (→ `last_status_change`,
`files_submitted`, `files_pending`) — is genuinely dynamic, and per
`docs/adr/0007-condor-q-primary-progress-source.md` is now fallback-only:
consulted only when `condor_q` itself can't be reached. In the common case
(`condor_q` up), a run only ever needs `jobsub_job_id` (to query `condor_q`
with) and `subgroup` (for `pro_available()`) — the rest of the payload is
paid for and thrown away.

## The fix

`PomsSession` caches `jobsub_job_id`/`subgroup` per `submission_id` in a
local JSON file, keyed by `campaign_stage_id`, and skips `submission_details()`
entirely for any active submission already in the cache:

- **Location**: `<cache_dir>/submission_cache_<campaign_stage_id>.json`; as of
  `docs/adr/0015-per-campaign-directory-under-logs.md`, `cache_dir` is
  `logs/<campaign_name>/`. No `config.ini` key for it — automatically
  `.gitignore`d (the whole `logs/` directory is) and automatically
  one-per-campaign-stage since `campaign_stage_id` is unique, without needing
  per-config bookkeeping the way `lock_file` once did (see
  `docs/adr/0006-lock-file-configurable-per-config.md`).
- **`campaign_stage_submissions()` still runs every time** — it's the only
  way to notice a submission made directly through the POMS UI, which the
  cache would otherwise never learn about.
- **Cache miss** (submission not yet in the file): `submission_details()` is
  called as before; if it returns a real `jobsub_job_id`, that plus the
  parsed `subgroup` get written to the cache immediately.
- **Cache hit**: `jobsub_job_id`/`subgroup` come straight from the file;
  `pct_complete`/`last_status_change`/`files_submitted`/`files_pending` are
  all `None` for that submission this run (no call was made).
- `_get_jobsub_job_id()` (used by `submit_next_slice()`'s post-launch poll)
  routes through the same fetch-and-cache helper, so a freshly-launched
  submission's info is cached the moment it's known — `get_progress()` never
  pays for it again on a later run.
- No pruning: a campaign stage has at most a few dozen submissions over its
  life, so the file never grows large enough to matter — same
  no-rotation simplicity as `log_file`.

## Consequence: `in_flight_submissions()` must not skip condor_q on `pct_complete=None`

Before this change, `pct_complete=None` meant "we have no information at
all" (a `submission_details()` failure), so `in_flight_submissions()`
short-circuited: treat as in-flight, don't even try `condor_q`. After this
change, `pct_complete=None` is the *normal* state for every cache-hit
submission — but `jobsub_job_id` is still known, so `condor_q` can still
resolve real progress for it. Keeping the old shortcut would mean any
submission whose static fields came from the cache gets stuck "in-flight"
forever, never advancing past `pct_complete_threshold`, defeating the point
of caching.

`_effective_pct_complete()`/`in_flight_submissions()` were restructured so
`condor_q` is attempted whenever `jobsub_job_id` is present, regardless of
whether `pct_complete` is known; the "no signal, stay in-flight" fallback
now only triggers when *neither* `condor_q` nor a POMS-side `pct_complete`
is available at all.

## Accepted tradeoff

A submission whose static fields come from the cache has no
`last_status_change`/`files_submitted`/`files_pending` this run, so if
`condor_q` is briefly unavailable for it, ADR-0007's layer 2 (stale-status
proxy) can't run either — it falls straight to "no signal, stay in-flight"
rather than layer 3's raw `pct_complete`. Judged acceptable: layer 2/3 are
themselves already just a fallback for `condor_q` outages, and this only
narrows that fallback further for cache-hit submissions, it doesn't remove
`condor_q` as the primary source for any submission.
