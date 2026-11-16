---
name: sbnd-poms
description: Submit and manage SBND POMS grid production jobs -- launch test/real submissions via the poms_auto_submit repo, read or patch a POMS campaign stage's param_overrides directly via poms_client, fetch+analyze grid job logs, and triage which jobs of a many-job DAG failed via condor history (including minting a production-role token when your personal token can't fetch logs). Use when submitting a POMS job, doing a test launch, checking or fixing a campaign stage's fcl/parameter overrides, or fetching/debugging SBND grid job logs, or finding out why many jobs failed (exit 127, held, killed) (jobsub_fetchlog, fife_wrap, ArtException, POMS launch errors like "extra command line arguments").
---

# sbnd-poms

Deterministic scripts, one per job. You decide which to run, in what
order, and with what campaign/stage/dataset/fcl/override values -- the
scripts don't guess any of that.

All scripts source `poms_auto_submit`'s `setup.sh`
(the repo root, found from the script's own location, four levels up from
`.claude/skills/sbnd-poms/scripts/`) themselves, which mints a bearer token via sbndpro's
managed-token credkey. Confirmed to work run as a regular user, not just
`sbndpro` itself. **Always invoke with `bash`/as `./script.sh`, never
`source`** -- see [REFERENCE.md](REFERENCE.md#gotchas).

## Scripts

**Submit a job** (test or real):
```bash
scripts/submit.sh <config.ini>            # dry-run only
scripts/submit.sh <config.ini> --real     # dry-run, then submit for real
scripts/submit.sh <config.ini> --real --follow-launch   # also print POMS's launch stdout while waiting for the jobsub id
```
Write the `.ini` yourself per `poms_auto_submit/configs/config.ini`'s
template. Use `test_launch=1` for any validation submission -- it routes
through the campaign stage's `test_param_overrides` instead of the real
ones. For a single manual real submission (not a cron/campaign run), set
`max_splits=1` and leave out `do_recovery` and `do_cleanup` (both off when
missing). `switch`, `last_split` and `recovery_handled` are no longer config
keys: the script keeps them in
`poms_auto_submit/logs/<campaign_name>/state_<ini name>.json` and never
writes the ini. After one real submit that file holds `last_split = 1`, so
reusing the same ini is skipped as "max_splits reached". Test launches
(`test_launch=1`) never change `last_split`, so a test ini can be re-run
freely and needs no `max_splits`. A test run only submits once (no planning, `last_split`, switch, lock, in-flight check, stragglers, recovery or cleanup); the subgroup comes from the stage's `test_param_overrides` (set it to `test` in POMS). For a real (non-test) ini, to submit again
set `"last_split": 0` in that state file (or delete the file).

**Read a campaign stage's current config** (dataset, split type,
param_overrides, test_param_overrides):
```bash
scripts/show_stage.sh <campaign_name> <campaign_stage_name> [--json]
```
Flags any override value with leading/trailing whitespace or a literal
`${` -- both break POMS's launch parser. Run this before touching
overrides, and again after, to confirm the fix landed clean.

**Show a stage's output definitions** (read-only, writes nothing):
```bash
scripts/show_output_definitions.sh <campaign_name> <campaign_stage_name> [--all-tags]
```
Prints the 9-step trace of how the stage's Output Definitions are chosen
(launch command, Input Dataset, lineage tags, Test Launch tags dropped,
postscript patterns, candidates, SAM verification, classification,
result), then name, depth, tier and file count. Names come from the
postscript and must be verified in SAM (files exist, first parents reach
the Input Dataset). A finished campaign POMS hides can be looked up with
`python poms_auto_submit/scripts/show_output_definitions.py --submission <id>`
(not a Test Launch id). Only fresh campaigns are in scope: a stage already
switched to a recovery dataset finds nothing. `--all-tags`
adds every tag found under the inputs with its newest file date. Use it to
check which definitions belong to a stage when its inputs were reused by
other campaigns. Real example:
`poms_auto_submit/docs/raw/output_definitions_trace_IntrinsicNuE_2026-11-15.txt`.

**Read a submission's launch stdout** (what the POMS launch page shows):
```bash
scripts/launch_log.sh <campaign_name> <campaign_stage_name> <submission_id> [--follow]
```
Calls POMS's `list_launch_file` endpoint (no `poms_client` wrapper) via
`PomsSession.launch_log()`. `--follow` polls until POMS stops writing the file.

**Patch specific param_overrides** (partial merge -- every other key is
left untouched):
```bash
scripts/update_stage_params.sh <campaign_name> <campaign_stage_name> \
    KEY=VALUE [KEY=VALUE ...]
```
`KEY` should NOT include the trailing `=` (the script adds it). Refuses
to write a value with leading/trailing whitespace or `${` unless you pass
`--force` -- see [REFERENCE.md](REFERENCE.md#gotchas) for why this is a
real, repeatedly-hit bug class, not a hypothetical.

**Fetch + analyze grid job logs**:
```bash
scripts/fetch_and_analyze.sh <jobsub_id> [outdir]        # fetches, then analyzes
scripts/fetch_and_analyze.sh --offline <outfile> [errfile]  # analyzes files you already have
```
Prints, in order: final exit status, fife_wrap-level failure signatures,
art/LArSoft exceptions with context, per-stage `TrigReport`/`MemReport`,
and the SAM metadata declare/copy-back check. **Check that last section
even on exit 0** -- a job can complete cleanly while silently failing to
declare/copy back any output.

**Triage a many-job submission** (which jobs failed, and where):
```bash
scripts/triage_dag.sh <dag_id>@<schedd> [outdir]      # condor history only
scripts/triage_logs.sh <logdir> [stage1,stage2,...]   # classify fetched logs
```
`scripts/missing_inputs.sh "<input query>" <output_def>` lists inputs that
produced no output (use it to count/identify the failed work per window).
`triage_dag.sh` needs no logs and buckets workers (ok / early_death /
late_fail / held / removed) per site. Run it *before* fetching anything,
then fetch only one example per bucket. Details and the failure modes seen
so far: [LOGS.md](LOGS.md). `fetch_and_analyze.sh --personal-token ...`
uses your own token instead of the production one (e.g. HNL jobs).

## Typical workflows

- **"Submit a test job"**: `show_stage.sh` to see current config →
  write/reuse a `test_launch=1` config.ini → `submit.sh ... --real` →
  once you have a `jobsub_job_id`, `fetch_and_analyze.sh <id>`.
- **"Job failed at launch with 'extra command line arguments'"**:
  `show_stage.sh` to find the bad override (whitespace/`${` flagged
  automatically) → `update_stage_params.sh` to fix it → resubmit.
- **"Many jobs failed"**: `triage_dag.sh` -> pick one job per bucket ->
  `fetch_and_analyze.sh` on those. Never fetch the whole DAG.
- **"Job ran but crashed / didn't declare output"**: `fetch_and_analyze.sh
  <jobsub_id>` and read the art/LArSoft exceptions + metadata sections
  first, before opening raw logs by hand.
- **Logs already handed to you** (fetched elsewhere, or found under a
  DAG job's nested per-node dir): `fetch_and_analyze.sh --offline`.

## See also

- [REFERENCE.md](REFERENCE.md) -- gotchas (override whitespace/`${`
  parser bug, `set -u` vs UPS, DAG log layout, `jobsub_fetchlog` 401s)
  and the poms_auto_submit repo's own docs/ADRs for anything these
  scripts don't cover (recovery, cleanup, stragglers, cron operation).
- [LOGS.md](LOGS.md) -- DAG triage workflow and failure-mode catalogue.
- SBND-Triton-DNN-ROI domain gotchas (fcl pairing, jsonnet sibling
  imports) live in the sibling repo `../triton_sbnd/grid/` and
  `../triton_sbnd/docs/` -- not duplicated here.
