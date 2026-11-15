# A Campaign Snapshot answers every in-flight question once per run

`run()` used to call `session.get_progress()` up to four times per run (planning,
stragglers, cleanup, recovery), and each pass re-ran `condor_q` per Submission
through a differently-parameterised copy of the same in-flight rule. The same
fact ("what is the campaign doing right now") could be answered differently
within one run, and the helpers (`_plan`, `_next_slice_count`, `_plan_subgroups`,
`_in_flight_submissions`, `_any_still_running`, `_cleanup_ready`,
`_recovery_ready`) existed mostly so tests could reach them.

## Decisions

- **One module, `scripts/campaign_snapshot.py`.** `CampaignSnapshot(cfg,
  submissions, get_condor_progress)` (or `CampaignSnapshot.take(cfg, session)`).
  Its interface is `slices_to_plan()`, `any_still_running(threshold)`,
  `cleanup_ready()`, `recovery_ready()`, `entries()`/`condor_error` (for
  `stragglers.manage`) and `record_stuck()`. It never imports `poms_auto_submit`.
- **Lazy, cached `condor_q`.** Progress is queried on first need, once per
  Submission, and stops at the first error. A run with no splits left and no
  cleanup or recovery enabled still makes no `condor_q` call.
- **Read-only.** Stuck tracking (the `stuck_no_data` State File section) moved
  out of the in-flight query into the explicit `record_stuck()`, which `run()`
  calls when not `--dry-run`. It keeps its old gate: skipped when no splits are
  left or `condor_q` errored.
- **Immutable per run.** After `submit_plan` the run takes a fresh snapshot for
  the recovery check, because a launch changed what is in flight.
  `plan_next_slices(cfg, session)` stays as a thin entry point that builds its
  own snapshot, so `recovery.py` is unchanged.
- **No rule changes.** The slot rules of ADR-0005, 0013, 0015 and 0017 and the
  gates of ADR-0016, 0018 and 0019 are unchanged; their `_cleanup_ready`,
  `_recovery_ready`, `_in_flight_submissions`, `_any_still_running` and
  `_no_splits_left` now live in `campaign_snapshot.py` as `CampaignSnapshot`
  methods and `no_splits_left()`.

## Deferred

The `poms_auto_submit` <-> `recovery`/`cleanup` import cycle and a dedicated
`condor_q` seam wait until the recovery and cleanup steps are reworked.
