# Decide slice count and pro subgroup from "in-flight" submissions, not a same-run-only view

**Extends ADR-0002**, which is still correct about *why* `param_overrides`
can't be read back for pro state — this ADR changes what `plan_subgroups()`
checks instead, and changes `next_slice_count()`'s trigger for topping up.

## The problem with the old `next_slice_count()`

The old decision only ever acted once at least one active submission had
crossed `pct_complete_threshold` (`ready_count == 0` short-circuited to "skip
this run," ignoring how far below target the actual in-flight count was).
Concretely: raising `submit_two_slices` from 0 to 1 mid-campaign did nothing
until the one already-running submission happened to cross threshold on its
own — the script never proactively topped up toward the new target.

## The problem with the old `plan_subgroups()`

`plan_subgroups(num_slices, role)` decided pro/standard purely from what
*this run* was about to submit — a lone new slice always took pro, a same-run
pair split one/one. That's correct within a single run, but says nothing
about whether a *previous* run's still-active submission already holds pro.
Nothing stopped two submissions from different runs both holding pro
concurrently, violating CONTEXT.md's Subgroup entry ("only one slice at a
time").

## The fix: "in-flight" as the shared concept for both decisions

An active submission (`New`/`Idle`/`Running`/`Held`) counts as **in-flight**
only while it's still under `pct_complete_threshold` — once it crosses that,
it's treated as good as done and stops occupying a slot, same logic ADR-0002
already accepted for the pro handoff (submit the replacement at threshold,
not at `Finished`).

- `next_slice_count()` now submits `target - len(in_flight)` new slices
  (floored at 0), where `target` is 1 or 2 (`submit_two_slices`) capped by
  remaining `max_splits`. This tops up toward target immediately regardless
  of whether anything's crossed threshold yet, and collapses the old
  three-branch logic (empty / none-ready-skip / general case) into one
  formula — `submissions == []` is just the `len(in_flight) == 0` case.
- `plan_subgroups()` gained a `pro_available` argument:
  `pro_available(in_flight) = not any(s["subgroup"] == "pro" for s in in_flight)`.
  A new slice can only take pro if no *currently in-flight* submission
  already holds it — regardless of which run submitted that one. Once an
  in-flight pro-holder crosses threshold, it stops counting as in-flight and
  the slot frees up immediately, even though it may still be technically
  `Running` until it finishes (same accepted-risk brief-overlap window
  ADR-0002 already signed off on).

`subgroup` itself is read from `command_executed`'s `--subgroup=` flag
(`PomsSession.get_progress()`), not from `param_overrides` — see
`docs/poms_client_gotchas.md`'s "Where `subgroup` actually lives" for why:
the stage's *current* `param_overrides` is exactly the unreliable read
ADR-0002 rejected, and Test Launches don't use it at all server-side.

`run()` deliberately still calls `session.set_subgroup(use_pro)` every run
regardless of `test_launch` — it has no effect on what a Test Launch
actually submits (server substitutes `test_param_overrides`), but keeps the
plan/decision logic exercised and consistent whether or not `test_launch` is
on, rather than special-casing it away. Don't "simplify" this into a
test_launch-gated call.

## Consequence: bootstrapping to a higher target can still submit >1 at once

If there's currently only one in-flight submission and `target` is 2 (e.g.
`submit_two_slices` was just turned on), once that submission crosses
threshold `len(in_flight)` drops straight to 0 and `next_slice_count()`
submits 2 new slices in the same run — not 1. This isn't new: it's the same
behavior the pre-existing test suite already locked in for the
single-submission-graduates-with-target-2 case. The in-flight redefinition
only removes the *eager top-up* gap above; it doesn't smooth out a
below-target bootstrap into gradual one-at-a-time increments.
