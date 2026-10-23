# Rewrite the `new()` split's `firsttime` before every launch

A Campaign Stage whose Input Dataset keeps growing can use POMS's `new()`
split type: each launch takes the files that arrived in one `window`
starting at `firsttime`. For a cron that launches every N hours or days,
`firsttime` has to move between launches, and POMS does not do that itself.

## Decision

An optional `[split]` section in the ini. When present, `submit_plan()` calls
`PomsSession.set_split_type()` before every `launch_jobs`, writing

    new(window=1h,round=1,fts=1800,localtime=1,firsttime=<unix>,lasttime=<unix>)

with `firsttime = first_start + last_split * step` (local time), so the
sequence of windows is fixed by the ini and indexed by `last_split`, the same
way the launches themselves are counted. Without the section nothing changes.

- `lasttime` is a constant far in the future (2031-04-09): `firsttime` plus
  `window` already bounds the chunk, so `lasttime` never needs to move.
- Sequence from the ini, not `now - window`: a clock-based start cannot
  reproduce a back-to-back test of 00-01, 02-03 and 04-05 AM windows.
- `set_split_type()` leaves `cs_last_split` alone. POMS remembers its position
  in the windows; how a changed `firsttime` interacts with that counter is
  checked live (see `docs/poms_client_gotchas.md`) before any cron relies on it.
