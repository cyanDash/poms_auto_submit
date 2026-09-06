# Cleanup gates on the last slice's status and pct_complete, not just max_splits

Implements TODO.md's "Add File Cleanup feature": once a campaign stage
(including any recovery slices) is done, automatically submit
duplicate-cleanup jobs for every output dataset recovery ever recorded, then
turn the campaign stage off.

## Why "no more splits left" alone is wrong

The obvious trigger is `last_split` reaching `max_splits` (`_no_splits_left()`,
also used by `_plan()`'s own "max_splits reached" early return). That's
wrong on its own: `submit_plan()` increments and persists `last_split`
**the instant a slice is submitted**, not once it finishes. Gating cleanup
on `_no_splits_left()` alone would fire duplicate-cleanup jobs against the
last recovery slice's output datasets while that slice might still be
sitting at, say, 75% complete — running `sam_delete_duplicates` against an
Input Dataset that hasn't finished producing its outputs yet.

## The fix

`_cleanup_ready(cfg, session)` requires all of:

- `cfg["do_cleanup"]` (opt-in per campaign stage)
- `cfg["recovery_handled"]` (recovery has already been evaluated)
- `_no_splits_left(cfg)`
- the last submission's status is in `RECOVERY_ELIGIBLE_STATUSES`
  (`{"Completed", "Located"}` — the same set `recovery.py` already gates on
  before evaluating a recovery dataset, imported from `recovery` rather than
  redefined)
- that submission's `_effective_pct_complete()` (the same
  condor_q-primary/POMS-fallback chain everything else uses) is > 98%

The status check specifically guards against a `Failed`/`Cancelled` last
slice: such a submission can still carry a high, stale `pct_complete`
reading from before it died, and that must route to manual review, not into
an automatic delete-duplicates run.

## No separate `cleanup_handled` flag

Unlike `recovery_handled`, cleanup needs no persisted handled-flag of its
own: `run_cleanup()`'s last step is to persist `switch=0`, and `main()`
already refuses to call `run()` at all once `switch` is off. That makes
cleanup naturally one-shot — the next hourly cron tick just logs "switch is
off" and returns.

## Test-launch output defnames never reach the file cleanup reads

`output_definitions_{stage_id}.txt` has no per-line marker for whether an
entry came from a test-launch run. Rather than filter at read time (which
would need such a marker), `recovery.py` diverts a test-launch's write to a
sibling `output_definitions_{stage_id}_test_launch.txt` instead. This is
safe because `run_recovery.sh` only *writes* that path (building the
recovery dataset's dimension from its own in-memory array) and never reads
it back, so redirecting the destination doesn't change its behavior.
`cleanup.py`'s reader also deduplicates (`_load_output_definitions()`,
first-seen order) since the same dataset can legitimately be recorded by
more than one recovery evaluation over a campaign's life.
