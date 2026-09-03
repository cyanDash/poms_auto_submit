# _in_flight_submissions() must gate on status before evaluating progress

## The problem

Confirmed live 2026-09-02: a manually-killed submission (`status=Failed`,
`condor_q` no longer tracking it, POMS's own `pct_complete` never recorded)
was still counted as in-flight:

```
progress: submission_id=3143716 status=Failed pct_complete=None (none) ...
decision: submit 0 slice(s) (in_flight=1 target=1) subgroup=[]
```

`_in_flight_submissions()` (`poms_auto_submit.py`) iterated over whatever
`PomsSession.get_progress()` returned and judged in-flight-ness purely from
`_effective_pct_complete()`, with no `Failed`-checked field/`status` gate at
all. `get_progress()`'s own fallback (`target = active if active else
[submissions[-1]]`, for when nothing is currently active) can hand back a
single **non-active, terminal-status** submission just for reporting
purposes — but `_in_flight_submissions()` had no way to tell "this is here
for display" apart from "this is really still running." Combined with "no
signal at all counts as in-flight, conservatively" (ADR-0005/0007's
deliberate choice for genuinely active-but-stuck submissions), a terminal
submission with no recorded progress got stuck being treated as in-flight
**forever** — silently capping `num_slices` at `0` on every future run, with
no error, no expiry, and no path to `submit_next_slice()` ever being called
again (which also means the recovery trigger in `docs/adr/0010` could never
fire either, since that only hooks off `submit_next_slice()` actually being
attempted and POMS rejecting it).

## The fix

`_in_flight_submissions()` now skips any submission whose `status` isn't in
`ACTIVE_SUBMISSION_STATUSES` (`{"New", "Idle", "Running", "Held"}`) before
ever computing its progress signal — a terminal status (`Failed`,
`Cancelled`, `Removed`, `LaunchFailed`, `Completed`, `Located`, `Awaiting
Approval`, `Approved`) is never in-flight, regardless of whether
`pct_complete`/`condor_q` data is available for it. This was always the
intent (CONTEXT.md's Status entry: "Decision logic reads `pct_complete` off
`New`, `Idle`, `Running`, and `Held` Submissions" — an enumerated, closed
set), just not actually enforced in `_in_flight_submissions()` itself.

## Consequence

A submission that fails outright (killed, crashed, `LaunchFailed`, etc.)
now correctly frees its slot on the very next run, instead of permanently
stalling further submissions. `_next_slice_count()`/`plan_next_slices()`'s
existing "no signal counts as in-flight" behavior is preserved for
genuinely *active* submissions (`New`/`Idle` with no `pct_complete` yet,
or `condor_q`/POMS both failing to report progress on a `Running`/`Held`
submission) — this fix narrows the ambiguous case to only apply where the
submission is actually still active, not to every submission regardless of
status.
