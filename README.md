# poms_auto_submit

Cron script for SBND production: checks how far a POMS campaign
stage's Running submissions have progressed, decides how many new slices (0,
1, or 2) are ready to go out, and submits them via POMS. Every decision is
logged. Assumes that you have a well-tested campaign ready to be launched.

## What it does, each run

1. **Check progress** — reads back status, `pct_complete`, and `subgroup` for
   every currently-active submission (or the latest one, if none are active).
2. **Decide how many** — submits enough new slices to bring the number of
   **in-flight** submissions (active and still under `pct_complete_threshold`)
   up to target (1, or 2 with `submit_two_slices`), capped by remaining
   `max_splits`. A submission that's crossed `pct_complete_threshold` no
   longer counts as in-flight, freeing its slot for a replacement even before
   it finishes. `last_split` is a counter the script maintains itself in
   its state file, `logs/<campaign_name>/state_<ini name>.json`;
   edit it there to reset or correct the count.
3. **Decide subgroup, submit** — only one submission may hold the
   higher-priority `pro` subgroup at a time. A new slice takes it if no
   in-flight submission already does; when 2 slices go out in the same run,
   at most one of them gets `pro`.
4. **Recover on exhaustion** — once POMS reports the Input Dataset
   exhausted and the last slice has finished, builds and submits a recovery
   slice for input files that never made it into an output dataset (see
   TODO.md item 1).
5. **Clean up duplicates, then switch off** — with `do_cleanup = 1`, once
   no splits are left and nothing is still running, retires duplicate
   output files in SAM and turns the campaign off in its state file (see
   [Cleanup](#cleanup)).

`--dry-run` logs what would happen without calling POMS to update params or submit anything. When cleanup is due it writes the cleanup plan and retires nothing.

## Setup

Must be run as the `sbndpro` user — it has managed tokens configured, so
the setup script can fetch a bearer token with `htgettoken` alone, no `kinit`
needed. Clone the repo in your working area.

```bash
git clone https://github.com/cyanDash/poms_auto_submit.git
cd poms_auto_submit
source setup.sh
```

## Configure

Copy the `configs/config.ini` template to your own ini file to make changes
according to your campaign.

```ini
[poms]
experiment = sbnd
campaign_name = override_me
campaign_stage_name = override_me

[decision]
; what percentage of the total number of jobs must be completed before the
; next slice is submitted; 0-100
pct_complete_threshold = 70

; 0: keep 1 slice in flight at a time. 1: keep 2 slices in flight at a time.
submit_two_slices = 0

; total number of slices this campaign stage needs; submission stops once
; the script has submitted that many (it keeps the count in its state file)
max_splits = 5

; when true, a run is only a Test Launch: it submits once with the stage's
; test_param_overrides (set its subgroup to `test` in POMS) and does nothing
; else -- no planning, in-flight checks, last_split, switch, lock, stragglers, recovery or cleanup
test_launch = 0

; when true, automatically build a Recovery Dataset and submit it once the
; Input Dataset is exhausted. Never changed by the script.
do_recovery = 1

; when true, automatically retire duplicate output files in SAM once the
; campaign is fully done (nothing is deleted from disk), then turn the
; campaign off in its state file.
do_cleanup = 0
```

Set `campaign_name`/`campaign_stage_name` to a campaign stage you own.

The script never writes to the ini. Everything it tracks itself lives in one state file per ini, `logs/<campaign_name>/state_<ini name>.json`: the `last_split` counter, the `switch` that cleanup turns off when the campaign is done, the recovery flag, and its caches. To stop a campaign, stop its cron job. To re-run an ini that cleanup has switched off, set `"switch": true` in its state file.

`submit_two_slices = 1` implies a pro and a non-pro submission can be 
simultaneously run.

If your input dataset has 43k files and each job submits 10k files, then POMS will create 5 splits (slice 0-4). In that case `max_splits = 5`.

## Cleanup

A grid job that is retried can leave two sets of output files for one input.
With `do_cleanup = 1`, the script cleans this up once the campaign is done
(no splits left, nothing running, and recovery handled when `do_recovery = 1`).
It never runs with `test_launch = 1`.

What it does, in one run, under the same lock:

1. **Find the output definitions** of this campaign stage (from the metadata
   postscript and from file lineage). If they cannot be found, it logs an
   error and tries again next run.
2. **Plan.** For each input file it picks one job (the *kept worker*) and
   keeps that job's files in every output tier. Files of that input from any
   other job are marked for retirement. If a deeper tier was written by
   other jobs (chained stages), one whole chain is kept and the other chains
   are marked. The plan is written to
   `logs/<campaign_name>/cleanup_plan_<ini name>.json`.
3. **Retire.** The marked files are retired in SAM, one definition at a
   time, deepest tier first. Before a definition's first file is retired,
   the full paths of its marked files are written to
   `logs/<campaign_name>/<definition>_retired_files.txt`.
4. **Verify.** Each input must have only its kept files live, with live
   parents.
5. **Switch off.** Only when verification passes, `"switch": false` is
   written to the state file.

Things to know:

- **Nothing is deleted.** Files are only retired in SAM. No location is
  removed and no file is deleted from disk. Delete them yourself later,
  using `<definition>_retired_files.txt` as the list.
- **Keep `<definition>_retired_files.txt`.** It is the only record of what
  was retired. SAM cannot list a retired file, not even with
  `availability: retired` (see `docs/poms_client_gotchas.md`), so no SAM
  definition of the retired files is made.
- **A retirement cannot be undone with a SAM query.** To put files back,
  declare them again from the paths in `<definition>_retired_files.txt`
  with fife_utils' `sam_add_dataset`. This has not been tried yet. Test it
  on a few files first.
- **Files under an already retired file of this stage are retired too.**
  SAM cannot read a retired file. A parent that SAM no longer has is taken
  as one of this stage's retired files only when it sits at the depth of
  one of the cleaned definitions. SAM cannot say which input the files
  under it belong to, so the plan lists them under the retired file's name
  instead of an input's. A missing parent at any other depth stops the
  cleanup (`ungrouped`).
- **A file that cannot be placed under an input stops the cleanup** before
  anything is retired (no metadata, no parent, or a wrong depth in
  `output_definitions`). The error names the files; the plan lists them as
  `ungrouped`.
- **A file with live children outside the plan is left in place** (for example
  another campaign used it as input). It is listed in the plan and the log
  as "left in place, has children outside the plan". It does not block the
  switch.
- **Any failure leaves the switch on.** The next run resumes the same plan.
  Look for `cleanup:` lines at `ERROR` level in the log; a failed
  verification names the inputs that are wrong.
- **The per-file detail is in its own log**,
  `logs/<campaign_name>/<campaign_stage_name>_cleanup.log` (appended to; a
  dry run's lines are marked `[dry run]`). The campaign log only gets the
  summary: started, totals per definition, the result, and that path.
- **Review first.** Run with `--dry-run` when cleanup is due. It writes the
  plan and the `<definition>_retired_files.txt` lists and retires nothing.
  Check that `fallback_used` is `false` and that the `retired` lists look
  right. A dry run reads SAM; it records no output definitions and no
  `cleanup` section in the state file.
- **A big campaign takes long.** The first run reads the metadata of every
  output file and retires files one by one. Hourly runs that start in the
  meantime skip with "previous run still active".

The state file's `cleanup` section tracks the work:

```json
"cleanup": {"plan_path": ".../cleanup_plan_<ini name>.json", "done": ["<definition>", "..."], "finished": false}
```

`done` lists the definitions already retired; `finished` turns `true` when
verification passes. **To restart a cleanup from scratch, delete the
`cleanup` section.** The next run makes a new plan. You need this when
verification keeps failing on a file that appeared after the plan was made,
or when the log says the unfinished plan is for other definitions. To run a
cleanup again on an ini that is already switched off, also set
`"switch": true`.

**Look up a stage's output definitions** without an ini or any write
(after `source setup.sh`):

```bash
python scripts/show_output_definitions.py --campaign <campaign_name> --stage <campaign_stage_name> [--all-tags]
python scripts/show_output_definitions.py --submission <submission_id> [--all-tags]
```

POMS hides the stages of inactive campaigns, so a finished campaign may not
be found by name; `--submission` needs only a Submission id and reads its
launch command and Input Dataset (`PomsSession.get_submission_source()`).

It prints the steps of the inference (launch command, input dataset,
lineage tags, Test Launch tags, postscript patterns, candidates,
verification, classification, result), each with the tags it keeps and drops and why, then each
definition's depth, tier and file count. `--all-tags` also lists every
lineage tag with the newest `create_date` seen for it and whether it was
accepted, rejected (another campaign's) or ignored (a Test Launch tag). The
same steps are logged at INFO, prefixed `output definitions: step N`, when
a run infers the definitions.

The software version is `--mdappversion`, else `MT_PROJECTVERSION`, else the
version of the `sbndcode`/`icaruscode` setup in the launch command; step 1
says which. If the postscript cannot be read, a tag is this stage's when it
carries the production name (leading `test_` ignored) or `MT_SAMPLE`, next
to the version (`<name>_<version>` or `<sample>_<version>`); step 8 shows the
scope used. With none of name, sample and version known, nothing is recorded.

## Example workflow

Validate against a real campaign before trusting it unattended:

```bash
source setup.sh
./scripts/poms_auto_submit.py --config configs/<config file> --dry-run
```
`-c`/`--config` point at the config file to use. A dry run fetches
information about the currently active submissions and prints out what it
would do given this information. It does not submit a new slice, nor does it
update the parameters for a stage.

Check `logs/<campaign_name>/poms_auto_submit.log` for the logged progress/status/decision,
then run for real once manually and confirm in the POMS page that the submission goes out:

```bash
./scripts/poms_auto_submit.py -c configs/config.ini
```

Then wire it into cron. Open the crontab:
```bash
crontab -e
```

And paste the following script:
```cron
SHELL=/bin/bash
0 * * * * cd /path/to/poms_auto_submit && git checkout -q main && source setup.sh && ./scripts/poms_auto_submit.py -c /path/to/config/file
```
Make appropriate changes for the file paths. You now have a crontab installed that runs at the first minute of every hour.

Check the logs on a daily basis during the campaign to notice errors.

Make sure to delete the crontab at the end of your campaign.

## Branches for special campaigns

`main` splits a campaign stage with whatever split type POMS already has. Two campaign styles need extra config keys that live only on their own branch, so check that branch out (and read its README and `configs/config.ini`) before using them:

- `input-dataset-per-slice`: each slice is its own pre-built SAM dataset, and the script sets the stage's Input Dataset before every submission (`input_dataset_template` in `[decision]`).
- `new-split-window`: the stage uses POMS's `new()` time-window split, and the script moves its start time forward for every submission (the `[split]` section).

# A note on the usage of LLM:
Approximately 99% of the code and commit messages in this repository are written by Claude.
I have tried my best to ensure transparency regarding the AI usage and also spent hours to ensure that this damn thing works.
I have also attached the CLAUDE.md and CONTEXT.md files so that if someone uses an LLM to explore this repo, the LLM can have a better idea about the domain-specific terms and the design decisions. To ensure efficient token usage and best coding practices while avoiding slop, I have taken the
help of [Matt Pocock's skills](https://github.com/mattpocock/skills). A tutorial for using the skills is linked [here](https://youtu.be/M6mYodf0dJM?si=BR_nni--bqirschO). These skills are a gateway to software development fundamentals, like **test-driven development**, **deep modular architecture** (fancy way of saying that your codebase should be dividable into interconnected submodules which are easy to understand in terms of what they do or depend on, but they hide away the complexities of implementation as much as possible) etc. I had the privilege of learning these concepts while working on this project and I hope you do as well.
