# Recover each time window as a Test Launch, from its own script

## Context

The Run 3 stage launches one `new()` window a day (ADR-0024): at 04:00 the
00:00-03:00 window of `run3_raw_data_ongoing`. Recovery in
`poms_auto_submit.py` runs only once no splits are left, so a 60-day
campaign would recover nothing until the end. Recovery is wanted once per
window, the same day.

A recovery launch through the normal path would go through the stage's
`new()` split, which would cut the Recovery Dataset by time again, and it
would count as a launch in `last_split`, shifting every later window.

## Decision

`scripts/window_recovery.py -c <ini>` runs from its own cron line (16:00,
twelve hours after the launch). It:

1. takes the window from the newest non-test Submission's command,
   `-Oglobal.sam_dataset=<input>_<uuid>_time_<first>__<last>`, the
   definition POMS made for that launch;
2. stops if the State File's `window_recovery` section already holds that
   window (a window is recovered once);
3. reads or infers the Output Definitions (ADR-0023) and uses the depth-one
   ones;
4. runs `run_recovery.sh` on the window with ratio threshold 1
   (`RECOVERY_RATIO_THRESHOLD`): any input without an output in every
   depth-one definition is recovered; none missing means no launch;
5. sets `-Osubmit.memory=16GB` in `test_param_overrides`, points the stage
   at the Recovery Dataset, makes a Test Launch, and points the stage back
   at its original dataset, also when the launch fails;
6. records `{window: {recovery_dataset, submission_id}}`.

It never touches `last_split`, `cs_split_type` or `param_overrides`. It
takes the ini's lock, so it never overlaps a `poms_auto_submit.py` run.

The window is recovered even if its Submission is still running (operator
decision 2026-11-15): files still being processed are launched again, and
their duplicates are left to the cleanup.

## Test Launch requirements, set by hand

A Test Launch uses the stage's `test_split_type` and `test_param_overrides`.
The operator sets `test_split_type` to None, and makes every test override
other than the subgroup and memory equal to the real one (no
`-Oglobal.prodtype=test`), so the recovered files get the real
`Dataset.Tag`. The script checks this before launching and refuses
otherwise, naming the overrides that differ. While the stage is set up this
way, a hand-made Test Launch writes real outputs too.

## Output definitions read under the window

On this branch `output_definitions.py` reads lineage under the launch's
own dataset (`--dataset_definition=` in the newest real Submission's
command), the 3 h window, not the whole growing Input Dataset. The window's
inputs mostly have children by the time of the recovery; a spread sample of
`run3_raw_data_ongoing` found 8 with children in 200 probes and took most of
a 28-minute inference. The scan stops at the first inputs whose
descendants show every postscript name (1 of 43 probed, live). A name the scan saw is
verified on the files it saw (they descend from the window), against the
window's inputs; only a name it did not see is sampled from SAM and checked
against the whole Input Dataset, listed then. A sample from SAM is the first
5 of the definition's full list: SAM took 4-5 min per
`defname: <name> with limit 5` and 0.1 s for the whole list. Measured
2026-11-15 on BeamLight: 1351 s before, 3.5 s after, most of it POMS.
`main` and `input-dataset-per-slice`
keep reading the whole dataset: their recovery runs once, after every slice
has finished.

## Consequences

- `run_recovery.sh` reports no recovery needed when the Recovery Dataset is
  empty, whatever the threshold.
- A window whose 04:00 launch failed is not recovered: the newest real
  Submission is then the previous window, which is already handled.
- `--dry-run` counts the window's inputs missing an output with a SAM query
  and reports the test-launch checks; it creates no definition and writes
  nothing to POMS or the State File.
