# Drop the campaign-wide concurrency cap (supersedes ADR-0001)

ADR-0001 scoped `get_active_submission_count()` to the whole Campaign, not just the watched Campaign Stage, so a busy sibling stage would hold back this stage's next slice too -- protecting against multiple Campaign Stages in one Campaign submitting independently and over-saturating the grid.

Reopened because:

- **The scenario it guards against doesn't happen under current SBND convention.** Per CONTEXT.md's Campaign Stage entry, production runs everything as a single Campaign Stage (gen→g4→detsim→reco1→reco2→caf as Executables) specifically to avoid chaining stages, because chaining means copying files back to dCache and back to a worker node, which has lost files before. There's no second, simultaneously-active Campaign Stage in practice for this to protect against.
- **The endpoint backing it is unreliable.** Live: two runs 13 minutes apart, no state change in between, same genuinely-`Running` submission -- `active_submission_count` read `1`, then `0`. The call didn't fail (no non-200/201 status); it silently returned a wrong count, which caused a spurious extra submission on the second run.
- **`get_progress()` already tells us what we need**, scoped to the Campaign Stage this script actually watches: whether it has submission history, and each tracked Submission's `pct_complete`. `next_slice_count()` now decides purely from that -- no second, campaign-wide POMS call, no second source of truth to disagree with the first.

Accepted risk: if SBND production convention ever goes back to chaining multiple Campaign Stages within one Campaign, this script would no longer hold one stage back for another being busy. Nothing enforces that at the code level anymore -- it would need a fresh cap, ideally against a more reliable signal than `running_submissions` proved to be.
