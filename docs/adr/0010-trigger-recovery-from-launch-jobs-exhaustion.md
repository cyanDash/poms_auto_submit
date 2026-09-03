# Trigger recovery from launch_jobs exhaustion, gated on last-slice status and a persisted flag

## The problem

TODO.md item 1 asks for `scripts/setup_recovery.sh`'s manual workflow to run
itself once a campaign stage's Input Dataset is exhausted. Two design
questions: what counts as "exhausted," and how to avoid redoing expensive
samweb/POMS work on every subsequent hourly run once it's been evaluated.

## The fix

Hook into `submit_next_slice()` returning `None`
(`docs/adr/0009-bypass-make-poms-call-for-launch-jobs.md`) — POMS's own
authoritative "no more splits" signal — not `plan_next_slices()` returning
`[]`, which can also mean the locally-configured `max_splits` cap was hit
first (a different, non-authoritative stop condition).

"Last slice completed" reuses `PomsSession.get_progress()`'s existing
fallback to the most recent Submission once nothing is active.
`recovery.RECOVERY_ELIGIBLE_STATUSES = {"Completed", "Located"}` — both are
success terminal states (CONTEXT.md's Status entry). Any other terminal
status (`Failed`, `Cancelled`, `Removed`, `LaunchFailed`, `Awaiting
Approval`, `Approved`) logs a warning and skips auto-recovery — nothing
about those states means "safe to build a recovery dataset and resubmit
unattended."

A persisted `[decision] recovery_handled` flag (same rewrite-in-place
mechanism as `last_split`) makes every run after the first full evaluation a
single boolean check. It's set on every *conclusive* outcome (no recovery
needed, recovery submitted, needs manual review) but deliberately not on
`run_recovery.sh` failure or "last slice still active" — those are
retryable, and marking them handled would silently stop retrying.

## Accepted gap: max_splits isn't resized for the recovery dataset

`evaluate_and_run_recovery()` resets our own `last_split` to `0` (then `1`
after the recovery slice goes out) to match POMS's freshly-reset
`cs_last_split`, but leaves `max_splits` untouched — whatever headroom is
left over from the original dataset's budget is what the recovery dataset
gets to work with. If it needs more slices than that, `run()` hits its own
local cap and stops, even though POMS could still serve more splits.
Deliberately not sized against the recovery dataset's actual file count:
`max_splits` is set by a human per real campaign, generously, well above
what any one dataset (recovery or original) is expected to need — the same
reasoning as the ADR-0009 case this file's original "Accepted gap" leaned
on. If `max_splits` binds before POMS's Input Dataset is genuinely
exhausted, `run()` stops calling `submit_next_slice()` and the recovery
trigger never fires either way; not guarded against here.

## Accepted: no recovery-of-recovery

Once the recovery dataset itself is exhausted, `submit_next_slice()` returns
`None` again and `evaluate_and_run_recovery()` runs again — but
`recovery_handled` is already `True` from the first round, so it
short-circuits to `"already_handled"` without re-evaluating the ratio for a
second recovery pass. Deliberate, not an oversight: a recovery round needing
its own recovery round is a signal something is wrong with the campaign
(not just "the grid had some ordinary failures"), and warrants a human
looking at it rather than the script silently chaining further automated
dataset surgery. Resuming automated evaluation requires manually resetting
`recovery_handled = 0`.

## Update (2026-09-02): `recovery_switch` removed

The feature originally shipped behind a second, independent `[decision]
recovery_switch` kill switch (defaulted off), gating the whole feature until
`PomsSession.set_recovery_input_dataset()`'s `update_campaign_stage` write
and `run_recovery.sh`'s samweb pipeline were both confirmed against live
POMS/SAM (see `docs/poms_client_gotchas.md`) — both have now been. The
switch is removed; recovery now runs unconditionally once triggered, gated
only by `recovery_handled` and the status checks already described above.
