# Record each window's slice Submission in the State File

## Context

`window_recovery` records the recovery Submission of each window in the
State File (ADR-0025), keyed by the window's SAM definition name
`<input>_<uuid>_time_<first>__<last>`. The slice that launched the window is
recorded nowhere: the `submissions` cache holds only `jobsub_job_id` and
subgroup per `submission_id`, with no window. The Run 3 plotter
(`campaign_progress_plotter`, branch `auto-run3-data`) reads condor history
of both Submissions of a window to plot exit status, memory and wall time of
their workers, so it needs the pair.

## Decision

`submit_plan()` writes `window_launches[<first>] = {submission_id}` after each
successful launch of a `[split]` stage, `<first>` being the window's
`firsttime` in unix seconds (`split_window.window_start()`). The same epoch
is the first number of the window definition name, so a reader joins the
slice to the recovery without asking POMS. The `jobsub_job_id` is not copied:
the `submissions` section already holds it per `submission_id`.

Windows launched before this change have no record. The operator adds them
to the State File by hand (Run 3: 2026-11-15, 2026-11-16).

## Consequences

- One more State File section, written only by `submit_plan()`; a launch
  without `[split]`, an empty dataset, a failed launch or an exhausted stage
  writes nothing.
- A `--dry-run` never reaches `submit_plan()`, so it writes nothing.
