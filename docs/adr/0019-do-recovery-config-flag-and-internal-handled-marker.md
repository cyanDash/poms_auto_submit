# `do_recovery` is a fixed config flag; "recovery handled" is internal state

`[decision] recovery_handled` did two jobs in one config key: the script
flipped it to `1` after a conclusive recovery evaluation (ADR-0010), and
operators pre-set it to `1` to opt a campaign out of recovery (ADR-0017).
A reader of the ini could not tell which of the two had happened, and the
script rewrote a key that otherwise reads as an operator setting.

## Decisions

- **`do_recovery` replaces it in the config**, with the same polarity and
  opt-in style as `do_cleanup`. The operator sets it once and the script
  never rewrites it. A missing key means off, so an old ini that still
  carries `recovery_handled = 1` runs with recovery disabled rather than
  launching a recovery nobody asked for. This flips the default: an old ini
  with `recovery_handled = 0`, or with no recovery key at all, used to run
  recovery and now does not until `do_recovery = 1` is added.
- **"Handled" is internal state**: the `recovery_handled` key of the
  per-ini state file (ADR-0020), set on the same
  conclusive outcomes as before (no recovery needed, recovery submitted,
  recovery submit failed) and still not on a `run_recovery.sh` failure or a
  failed plan. Setting it back to `false` forces a re-evaluation, which replaces
  "reset `recovery_handled = 0`" in ADR-0010's no-recovery-of-recovery note.
- **`_recovery_ready()` holds the gate**, next to `_cleanup_ready()`:
  `do_recovery` on, not yet handled, submission history present, nothing
  still running per `condor_q`. `evaluate_and_run_recovery()` no longer
  checks any of this and no longer returns `already_handled`/`waiting`; the
  trigger is unchanged (`submit_plan()` reporting exhaustion).
- **Cleanup waits on recovery only when recovery is on.** `_cleanup_ready()`
  requires the handled flag when `do_recovery` is on, and ignores it when
  off, matching what a pre-set `recovery_handled = 1` used to allow.

## Consequences

- An exhausted stage with `do_recovery = 0` logs "recovery: disabled" every
  run that reaches exhaustion, instead of stalling silently.
- A stage that is already handled logs the state file path every run that
  reaches exhaustion, since the ini no longer shows that state.
- With `do_recovery = 0` and `do_cleanup = 1`, cleanup passes its gate with
  no output-definitions file, logs "nothing to clean" and turns the switch
  off. Same as the old pre-set opt-out; fixed by issue #16's lineage-based
  discovery.
- A live campaign moved from `recovery_handled = 1` to `do_recovery = 1`
  after its recovery already ran needs `"recovery_handled": true` added to
  its state file by hand, or
  recovery is evaluated a second time at the next exhaustion.
- Reaching `max_splits` before POMS reports exhaustion never evaluates
  recovery, so with `do_recovery = 1` the cleanup gate stays shut. This is
  intended: `max_splits` lets an operator run a fixed number of submissions
  without finishing the campaign. Each such run logs "recovery: not done
  (max_splits reached before the splits were exhausted)".
