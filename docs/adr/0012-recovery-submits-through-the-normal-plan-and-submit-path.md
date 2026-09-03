# Recovery submits its first slice(s) through plan_next_slices()/submit_plan(), not a bespoke call

## The problem

The first version of `evaluate_and_run_recovery()` called
`session.submit_next_slice()` directly, once, right after
`set_recovery_input_dataset()` — skipping `session.set_subgroup()`
entirely. That meant the recovery dataset's first slice inherited whatever
subgroup override was last left on the stage (from the *original* dataset's
last submission), rather than a deliberate `pro`/`standard` choice, and
submitted exactly one slice regardless of `[decision] submit_two_slices` or
how many are actually appropriate right now.

## The fix

Once `set_recovery_input_dataset()` has pointed the stage at the recovery
dataset and reset `cs_last_split`/our own `last_split` to `0`, the stage
looks, from POMS's point of view, like any other ordinary campaign stage
mid-run — nothing about it is special anymore. So treat it that way:
`evaluate_and_run_recovery()` now calls the same
`plan_next_slices(cfg, session)` the main `run()` loop uses to decide how
many slices and which subgroup(s), then submits that plan via the same
`submit_plan(cfg, session, plan)` helper (extracted out of `run()` for this
reuse) — `set_subgroup()` then `submit_next_slice()` per planned slice,
persisting `last_split` after each. Recovery's first slice(s) now go through
the identical decision path — subgroup assignment (ADR-0002, ADR-0005),
`submit_two_slices`, in-flight accounting — that every other slice does.

## Consequence

`recovery.py` no longer hardcodes "submit exactly one slice" — it submits
whatever `plan_next_slices()` says is appropriate (1 or 2, depending on
config), correctly gets `pro` on the new dataset's first slice if no other
submission holds it, and — via the ordinary `submit_plan()`/`run()` loop on
later hourly runs — needs no special handling for a recovery dataset that
itself takes more than one slice to complete (see ADR-0010's "max_splits
isn't resized" and "no recovery-of-recovery" entries, which still apply
unchanged).

A `plan_next_slices()` `RuntimeError` right after the dataset switch (a
transient POMS hiccup) is treated as retryable, not conclusive: POMS's
Input Dataset is already the recovery one at that point, so the *ordinary*
next-hour `run()` call (not `evaluate_and_run_recovery()`) picks it up
normally regardless — `recovery_handled` is deliberately not persisted on
this path.
