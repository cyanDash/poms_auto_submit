# Feed manually pre-built per-slice datasets when split_type is None

## The problem

This campaign stage (`icarus-fake-data-john` branch) has `split_type = None`
in POMS. Every other campaign this script runs against relies on POMS to
divide one big Input Dataset into batches automatically; with `split_type`
unset, POMS never does that — `launch_jobs` would just try to consume the
entire Input Dataset in a single submission, which isn't workable at this
scale. The user worked around this by hand: building 23 separate SAM
datasets, one per slice, named
`jaz8600-Run4-offbeambnbminbias-rand12k-1_slice{0..22}_files500`, each
already sized to be exactly one submission's worth of files (500).

`poms_auto_submit` had no way to feed these in turn — `submit_next_slice()`
always submits against whatever Input Dataset is already configured
server-side on the campaign stage, and nothing in the script ever changed
that dataset outside the recovery path.

## The fix

`submit_plan()` now sets the campaign stage's Input Dataset immediately
before every `submit_next_slice()` call, via a new `PomsSession` method,
`set_input_dataset()`. The dataset name is computed from `[decision]
input_dataset_template` (a required key on this branch, e.g.
`jaz8600-Run4-offbeambnbminbias-rand12k-1_slice{n}_files500`) with `{n}`
filled in from `cfg["last_split"]` — `last_split` already counts slices
submitted so far and doubles directly as the next 0-indexed slice number, so
no separate counter or offset is needed.

Unlike `set_recovery_input_dataset()` (used by the recovery path), this new
method does **not** reset `cs_last_split` to 0. `cs_last_split` is POMS's own
split-position counter within a dataset; with `split_type = None` for this
stage, that counter isn't meaningful, so leaving it alone avoids doing
something to production state that has no defined effect here.

This branch is dedicated entirely to this one campaign and, per convention,
is never merged back to `main` (see `feedback_campaign_branch_merges`
memory) — so `input_dataset_template` is unconditionally required, not an
opt-in switch guarding other campaigns' behavior on `main`.

Auto-recovery is disabled for this campaign from the start
(`recovery_handled = 1` preset in its `config.ini`): a manually pre-sliced
campaign has no automatically-derivable `*_recovery_campaign` dataset to
build, and letting `evaluate_and_run_recovery()` run here would fight with
this feature over the stage's Input Dataset.

## Consequence

Each hourly run submits against the correct next pre-built slice dataset
without any POMS-side auto-splitting. `max_splits` (set to 23 for this
campaign) still gates total submissions exactly as before — once
`last_split` reaches it, `_plan()` stops planning further slices, same as
any other campaign.
