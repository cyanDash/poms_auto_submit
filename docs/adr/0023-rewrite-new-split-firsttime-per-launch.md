# Rewrite the `new()` split's `firsttime` before every launch

A Campaign Stage whose Input Dataset keeps growing can use POMS's `new()`
split type: each launch takes the files that arrived in one `window`
starting at `firsttime`. For a cron that launches every N hours or days,
`firsttime` has to move between launches, and POMS does not do that itself.

## Decision

An optional `[split]` section in the ini. When present, `submit_plan()` calls
`PomsSession.set_split_type()` before every `launch_jobs`, writing

    new(window=1h,round=1,fts=1800,localtime=0,firsttime=<unix>,lasttime=<unix>)

with `firsttime = first_start + last_split * step` (local time), so the
sequence of windows is fixed by the ini and indexed by `last_split`, the same
way the launches themselves are counted. Without the section nothing changes.

- `lasttime` is a constant far in the future (2031-04-09): `firsttime` plus
  `window` already bounds the chunk, so `lasttime` never needs to move.
- Sequence from the ini, not `now - window`: a clock-based start cannot
  reproduce a back-to-back test of 00-01, 02-03 and 04-05 AM windows.
- `set_split_type()` also resets `cs_last_split` to 0. POMS remembers its
  position in the windows: after a launch `cs_last_split` holds the end of the
  window served, and the next launch resumes there, ignoring a changed
  `firsttime`. Confirmed live 2026-10-23 (launch 2 asked for 02:00 CDT but got
  the window right after launch 1). With `cs_last_split=0` the new `firsttime`
  is honoured.

## Times are Central in the ini, GMT inside POMS

Operators write `first_start` in Central time (`America/Chicago`, independent
of the host's TZ). It is converted to a true unix instant. `localtime`
defaults to 0, not 1: confirmed live 2026-10-23, `localtime=1` made POMS render
`firsttime` as Central text in the SAM `create_date` dimension, which SAM reads
as UTC, so the window landed 5 hours before the intended one.
