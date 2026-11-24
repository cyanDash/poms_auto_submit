# Combine several windows into one launch; recover them together

## Context

Run 3, 2026-11-24. The Nov 19 to 24 hang (ADR-0027) left the launches three
days behind the calendar, and the planner launches one window per run. The
Nov 22 to 25 windows need to go out in one Submission, and the Nov 21 window
(already running) must be recovered together with them afterwards.

## Decision

**Combined launch.** The ini's optional `[combine]` section (`first_launch`,
`last_launch`, in `last_split` numbers) makes the run that reaches
`last_split == first_launch`, once the last window has ended:

1. build the SAM definition `<dataset>_combined_time_<first>__<last>` as the
   union of those windows (`(create_date > a and <= b) or ...`), not the
   span between them, which would also hold the hours the windows skip;
2. force the launch to `pro`, whatever other Submissions run (this launch
   only; `_plan_subgroups` is untouched);
3. point the stage at it, clear `cs_split_type`, launch, and put the stage's
   dataset and the next `new()` window back, also on failure;
4. set `last_split` to `last_launch + 1` itself, and write one
   `window_launches` entry per window, all with the one `submission_id`.

`submit_two_slices = 1` is still set in the ini so the plan has a slot while
the previous slice runs. A combined launch with no files counts the range and
launches nothing; a LaunchFailed one is not counted.

**Recovery of a span.** `window_recovery.py --since YYYY-MM-DD` (manual only)
makes `<dataset>_span_time_<first>__<last>`, the union of the window
definitions of every `window_launches` Submission from that date on, refuses
while anything of the stage runs, and recovers it as one window: one
Recovery Dataset of the inputs missing any depth-one output. The 18:00 cron
must stay off while a combined window is the newest, because its
no-argument mode would recover only that newest window.

## Consequences

- The State File's `last_split` jumps 4 at once; reading windows by
  `last_split` (README) still holds. Do not hand-edit it afterwards.
- The plotter keys `window_launches` by window start, so each of the four
  days shows the same Submission's workers; its per-day file counts come from
  SAM by date and stay right.
- Recovery of the span is recorded under the span definition's name; its
  epochs are the first window's start, so the plotter attaches that
  recovery to the first window of the span only.
- One Submission of several windows' files: more jobs than a daily slice.
