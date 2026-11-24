# Skip empty windows before launching; bound the jobsub id wait

## Context

Run 3 (`sbnd_beamlight_run3.ini`), 2026-11-19 to 2026-11-24. Windows Nov 19
and Nov 20 held no input files. For each, POMS accepted the launch, then:

- 3183139 (Nov 19): POMS set `LaunchFailed` 8 s after launch, but
  `submit_next_slice()` only noticed 38 h later, still inside its jobsub-id
  loop.
- 3183960 (Nov 20): POMS left it in `New` for good (still so on Nov 24).

The loop had no limit, so the run held the lock the whole time. Every 04:00
submit and 18:00 recovery logged "previous run still active (lock held)",
and the Nov 21 (94 files) and Nov 22 (35 files) windows were never launched.
A `New` Submission also counts as in flight at any age (ADR-0017), so even
after the process dies it blocks planning until cancelled in POMS.

## Decision

1. Before each launch of a `[split]` stage, `submit_plan()` counts the
   window's files in SAM (`defname: <stage dataset> and create_date > start
   and <= end`, UTC). A window with none is counted (`last_split` + 1)
   without calling POMS, and the next window is tried in the same run, so
   empty days no longer cost a day of lag. A window that has not ended is
   never counted. If SAM or the stage read fails, it launches as before.
2. `submit_next_slice()` waits at most `JOBSUB_ID_MAX_WAIT_SECONDS` (15 min)
   for the jobsub id, then warns and returns the `submission_id` as launched.

The `EmptyDataset` path stays as the backstop for a window that empties
between the count and the launch.

## Consequences

- No POMS Submission is created for an empty window, so none can stick in `New`.
- A slow POMS can no longer hold the lock for more than 15 minutes plus the
  rest of the run.
- Non-empty windows still follow `last_split`, not the date (README gotchas).
