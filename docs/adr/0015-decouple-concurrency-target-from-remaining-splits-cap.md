# Decouple the concurrency target from the remaining_splits cap

**Refines ADR-0005**, which introduced `target - len(in_flight)` but capped
`target` itself by `remaining_splits`. This ADR changes *where* that cap
applies.

## The bug

`_plan()` computed:

```python
target = min(2 if cfg["submit_two_slices"] else 1, remaining_splits)
num_slices = max(0, target - len(in_flight))
```

`target` was doing two jobs at once: the desired concurrency level (1 or 2
in-flight submissions) and the remaining total-submission budget
(`remaining_splits = max_splits - last_split`). When exactly one split
remained and `submit_two_slices` was on, `target` collapsed from 2 to
`min(2, 1) == 1`. If one submission was already in flight, that made
`target == in_flight`, so `num_slices` came out 0 -- the campaign's very
last slice got withheld even though the remaining budget (1) was exactly
enough to submit it, and doing so would only have brought `in_flight` up to
2, which is what `submit_two_slices` calls for anyway.

Observed live in `sbndpro`'s cron log for the icarus-fake-data-john
campaign on 2026-09-11: `in_flight=1 target=1`, `submit 0 slice(s)`, with
one submission still in flight and one split of budget remaining. The final
slice only went out once that in-flight submission crossed
`pct_complete_threshold` on its own and dropped out of `in_flight` --
needless delay for no reason tied to the actual budget.

## The fix

Keep `target` as the raw concurrency target, uncapped:

```python
target = 2 if cfg["submit_two_slices"] else 1
in_flight = _in_flight_submissions(...)
num_slices = min(max(0, target - len(in_flight)), remaining_splits)
```

`remaining_splits` now clamps the *number of new slices this run may
submit*, applied after subtracting `in_flight` from the true target -- not
before. This still enforces the same hard budget (`num_slices` never
exceeds `remaining_splits`), but no longer makes the budget masquerade as a
lowered concurrency target that can equal an already-in-flight count and
falsely read as "at target."

The `decision:` log line also now reports `remaining_splits` alongside
`target`, since `target` alone no longer reflects the budget cap the way it
used to.
