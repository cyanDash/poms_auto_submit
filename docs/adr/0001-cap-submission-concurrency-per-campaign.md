# Cap Submission concurrency per Campaign, not per Campaign Stage

**Superseded by [0003-drop-campaign-wide-concurrency-cap.md](0003-drop-campaign-wide-concurrency-cap.md).** Kept for history.

`get_active_submission_count()` counts active Submissions (`New`/`Idle`/`Running`) across the whole Campaign, ignoring which Campaign Stage each one belongs to — even though the script only watches a single Campaign Stage per run and the function itself is called with a `campaign_stage_id`. This is deliberate, not a bug: if a Campaign has multiple Campaign Stages, running slices for one stage should still hold back new slices for another, because grid throughput is a shared resource across the whole Campaign. Scoping the count to just the watched Campaign Stage would let each stage submit independently and over-saturate the grid. `campaign_stage_id` is consequently dropped as an argument to this function — it plays no role in the count.
