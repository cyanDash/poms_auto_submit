# Remove stalled Stragglers with condor_rm so recovery starts with nothing running

Recovery waits until no Submission is still running (see ADR-0017), so a DAG that
never finishes blocks it forever. Once no splits are left, a Submission with 50
or fewer unfinished jobs is tracked as a Straggler, and one whose unfinished
count fails to drop by 5 across 3 consecutive runs is removed with `condor_rm`.
The goal is that no job is running when the recovery dataset is built; the
removed jobs' input files simply land in the Recovery Dataset.

## Decisions

- **Unfinished** is `DAG_NodesTotal - DAG_NodesDone - DAG_NodesFailed`, not
  `DAG_NodesQueued`, which excludes nodes waiting on throttling or retries and
  can fluctuate.
- **Failures count as progress.** A node moving into `DAG_NodesFailed` lowers
  the count, which resets the stagger counter. Accepted because only "nothing
  running" matters, not whether jobs succeeded.
- **Count-based, not time-based:** 3 consecutive runs, with a timestamp kept per
  Submission for logging only.
- **Scope:** only in-window Submissions, only once `_no_splits_left`. Older
  Submissions stay a manual case and log a WARNING.
- **Held jobs get no exemption.** Held with no progress for 3 runs is a stall.
- **Automatic removal is irreversible.** `--dry-run` logs "would condor_rm" and
  never calls it; every real removal logs a WARNING with the job counts.
- State lives in its own per-Campaign-Stage file in `cache_dir`, separate from
  the ADR-0008 cache and the #11 stuck-count file, with the same load/save
  style and malformed-file tolerance.
