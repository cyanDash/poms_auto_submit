# Debugging grid job logs (read this for log / failure-triage tasks)

## Rule: never fetch a whole DAG to find out "did it work"
`jobsub_fetchlog` on a DAG id downloads every node's logs (GBs). Answer
"did the campaign work" from SAM file counts. Find *which* jobs failed from
condor history, then fetch only those.

## Workflow for a many-job submission
1. `scripts/triage_dag.sh <dag_id>@<schedd>` -- condor history only, no logs.
   Buckets: ok / early_death / late_fail / held / removed / active, tallied per
   site, with an example job per cell and the exit codes. Also lists the hosts
   behind early deaths and counts retried workers.
2. Pick one example job per non-ok bucket from that table.
3. `scripts/fetch_and_analyze.sh <cluster>.<proc>@<schedd>` on those few
   (or `--offline` if the logs are already on disk). A single-job fetch of
   a DAG node downloads the whole DAG; the script now analyzes only the
   requested node and its retries (`--all` to override). Retry logs are
   `<cluster>.<proc>.vN.out`: read the un-suffixed (first) attempt too.
4. If logs for the whole DAG are already on disk:
   `scripts/triage_logs.sh <logdir> <stage1,stage2,...>` classifies every
   `*.out` by last `fife_wrap` exit status and by the lar stage that was
   running (number of `Art has completed` lines). Exit-0 logs are dropped
   from the summary.

## Which inputs produced no output
`scripts/missing_inputs.sh "<input dimension query>" <output_definition>`
lists input files that are not parents of any file in the output definition
(`isparentof`, not `ischildof`; the latter returns nearly every input).
Run it per submission window, then map the files to DAG nodes.

## Failure modes seen (torch-vs-Triton comparison campaign, Oct 2026)
- **exit 127, wall time seconds, no log (early_death).** HTCondor's
  `ExitCode` is the only evidence; the job died before fife_wrap wrote
  anything. 228 jobs, all on ONE Colorado host, lnxfarm246 (triage_dag.sh prints
  the host table). Missing command unconfirmed. Treat as a bad node:
  exclude the site/host rather than debug the job. Sibling jobs that landed
  on other Colorado hosts did not fail this way.
- **exit 1 after hours, log present (late_fail), "executable was killed".**
  The Triton-stage `lar` opened its input and died before the first event,
  with no gRPC/segfault text in the logs. 40 jobs, mostly Colorado.
  Torch half is already declared in SAM -> orphan torch files with no Triton
  sibling must be retired (`samweb retire-file`) before a recovery run.
- **condor ExitCode 0 but fetched log shows fife_wrap exit 1.** The job was
  retried (`NumJobStarts` > 1); the log is from the earlier attempt.
- **inputs missing outputs, DAG shows every node done, 0 failed; condor exit 88
  (late_fail, NumJobStarts 2) (SBND2026A BeamLight, Oct 2026).** First attempt
  was held by condor: `LastHoldReason` = `Docker job has gone over memory limit
  of 10240 Mb` (fife_wrap logs `executable was killed: exiting 1`; no postscript
  ran). SAM marks that consumer process `bad`; the retry (`<proc>.v1.out`)
  prints `Existing process ... is in state bad; cannot restart it`, then
  `Got consumerid of: ` (empty), so lar runs with `--sam-process-id=` and exits 88
  before any stage. condor reports only the last attempt, so the node looks
  clean. Per-stage VmHWM was only 1-2 GB, so the 10 GB is not visible in the
  art MemReport. Fix: raise the stage's memory request; the 3 inputs then
  come back through recovery. `triage_dag.sh` prints the hold reasons;
  `fetch_and_analyze.sh` flags the bad-process/empty-id lines.
- **exit 9 after 8-13 min, log present (late_fail), broken libGL on one host
  (SBND2026A Run 3 BeamLight, Submission 3181441, Nov 2026).** 6 of 43 jobs,
  all on ONE UChicago host, `iut2-c287.iu.edu` (same glidein). The first `lar`
  stage fails in `JobSetup` before any event: `Unable to load requested library
  .../libsbndcode_Decoders_PMT_SBNDPMTDecoder_module.so` /
  `/usr/lib64/libGL.so.1: undefined symbol:`. A bad node, not the job config:
  the other jobs at UChicago succeeded. No parameter change needed; the
  inputs come back through recovery (fails again only if it lands on the
  same host).
- **fife_wrap exit 0 is not success** -- always read the metadata
  declare/copy-back section.

## Useful condor_history fields
`ExitCode ExitBySignal JobStatus RemoteWallClockTime MATCH_GLIDEIN_Site
LastRemoteHost ResidentSetSize NumJobStarts Out HoldReason RemoveReason
LastHoldReason LastVacateTime`. Use `LastHoldReason` (not `HoldReason`) for
a job that was held and then retried: `HoldReason` is only set while held.
Query: `condor_history -pool gpcollector03.fnal.gov -name <schedd>
-constraint 'DAGManJobId==<id>' -af:t <fields>` (works with the production
token). DAG bookkeeping nodes have `Out` starting `sambegin`/`samend`.
