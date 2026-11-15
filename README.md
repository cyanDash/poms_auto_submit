# Run 3 BeamLight campaign: operating manual

Runs `poms_auto_submit` for `Auto_POMS_SBND2026A_Gen2_Run3_BeamLight`, stage
`decode_reco1_reco2_caf`, as `sbndpro`, and plots the daily output counts (done by another repo). Two
cron jobs: submit at 04:00, plot at 01:00 (host local time, Central).

| | |
|---|---|
| campaign_id / campaign_stage_id | 11649 / 27154 |
| Input dataset (stage `dataset`) | `run3_raw_data_ongoing` (grows with new files) |
| Split type | `new()` time window, driven by the ini's `[split]` section |
| Submit code | this repo, branch **`new-split-window`** (the `[split]` code lives only here) |
| Plot code | `/exp/sbnd/app/users/sbndpro/mcp/sdas1/campaign_progress_plotter`, branch **`auto-run3-data`** |
| Output base dir | `/pnfs/sbnd/scratch/users/sbndpro/poms_production/` (set in POMS `param_overrides` and `test_param_overrides`; not in the ini) |
| Plot output | `/exp/sbnd/app/users/sbndpro/mcp/SBND2026A/srcs/sbnutil/keepups/sbnd/data_run3/plots/sbnd_data_run3_keepup_{daily,weekly}.pdf` |

## 1. Submission (`poms_auto_submit`)

1. Check out the branch and set up, as `sbndpro` (this mints the production token):
   ```bash
   cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/poms_auto_submit
   git checkout new-split-window
   source setup.sh
   ```
2. Copy `configs/config.ini`, already filled in for this campaign, to your own
   ini (inis other than `config.ini` are gitignored):
   ```bash
   cp configs/config.ini configs/<ini>
   ```
   Check these values:
   - `first_start = 2026-11-15 00:00`
   - `step = 1d`
   - `window = 3h`
   - `max_splits = 60` (one launch per day, so the number of days to run)

   The 04:00 run on date D takes the files created that day between 00:00 and
   03:00. Slice n is `first_start + n*1d`, so the first run, at 04:00 on
   2026-11-15, submits 2026-11-15 00:00–03:00. `configs/config.ini` documents
   the other keys.
3. Check that `last_split` is 0. It lives in
   `logs/Auto_POMS_SBND2026A_Gen2_Run3_BeamLight/state_<ini name>.json`, one
   state file per ini (a new ini starts at 0). Edit the file to reset it.
4. Preview the slice's size. Get the window's start and end in GMT with
   `date` (Central in, GMT out), here for the 2026-11-15 slice:
   ```bash
   date -u -d 'TZ="America/Chicago" 2026-11-15 00:00' +%Y-%m-%dT%H:%M:%SZ   # <start Z>
   date -u -d 'TZ="America/Chicago" 2026-11-15 03:00' +%Y-%m-%dT%H:%M:%SZ   # <end Z>
   ```
   Then count the input files in it:
   ```bash
   samweb count-files "defname: run3_raw_data_ongoing and create_date > '<start Z>' and create_date <= '<end Z>'"
   ```
   A 3h window holds roughly 26–40 files. An empty window fails to launch.
5. Dry run, then one real run by hand:
   ```bash
   ./scripts/poms_auto_submit.py -c configs/<ini> --dry-run
   ./scripts/poms_auto_submit.py -c configs/<ini> --follow-launch
   ```
   `--dry-run` logs the decision without touching POMS. `--follow-launch`
   prints POMS's launch output until the jobsub id appears. The decision log is
   `logs/<campaign_name>/poms_auto_submit.log`. Confirm in the POMS page that
   the submission went out. If you launched a window you didn't want,
   `jobsub_rm -G sbnd --jobid=<id>` removes the cluster.
6. Install the cron line (step 3 below), starting 2026-11-15.

## 2. Plot (`window_output_plotter.py`)

`scripts/window_output_plotter.py daily` plots yesterday's window: for each of
the five `data_SBND2026A_…` tiers (`reco1`, `larcvreco1`, `histreco2`,
`flatcaf`, `caf`), the output file count against the input count for the
window that started at 00:00 the day before. The 01:00 run on Nov 16 plots
Nov 15 00:00–03:00, the slice submitted at 04:00 on Nov 15, which has had 21
hours to finish. `weekly` plots the last 7 windows. An optional date argument
replaces "today"; the date comes from the system clock, so a missed day
doesn't shift the plot.

1. Check the constants at the top of `scripts/window_output_plotter.py`. They
   are hard-coded and **must match the ini**: `first_start`
   (`2026-11-15 00:00`; its time of day sets the plotted window), `step` (1d),
   `window` (3h), plus `input_dataset`, `campaign`, `version` and `tiers`.
   Edit and commit them on `auto-run3-data`.
2. Try it by hand (needs no token; it only reads SAM):
   ```bash
   cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/campaign_progress_plotter
   git checkout auto-run3-data
   source setup.sh
   python scripts/window_output_plotter.py daily 2026-11-16
   ```
   Before the first full window has passed, it exits with "No window has
   started before <date>; nothing to plot." Output is
   `sbnd_data_run3_keepup_daily.pdf` (or `_weekly`) in the plots directory
   above, overwritten on each run.
3. Install the cron line on 2026-11-16, or let it print "nothing to plot" on
   Nov 15.

## 3. Cron

Only after a manual dry run and a manual real run look right. Open `crontab -e`
and paste:

```cron
0 4 * * * cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/poms_auto_submit && git checkout -q new-split-window && source ./setup.sh && ./scripts/poms_auto_submit.py -c configs/<ini>
0 1 * * * cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/campaign_progress_plotter && git checkout -q auto-run3-data && source ./setup.sh && python scripts/window_output_plotter.py daily
5 1 * * 1 cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/campaign_progress_plotter && git checkout -q auto-run3-data && source ./setup.sh && python scripts/window_output_plotter.py weekly
```

- Use `source ./setup.sh`, not `source setup.sh`. Cron runs lines with
  `/bin/sh` unless the crontab sets `SHELL=`, and in that mode `source` searches
  `PATH` for a bare name and fails with `setup.sh: file not found`.
- The checkout in each line stops a stray branch from changing the code that
  runs. If it fails (uncommitted edits to tracked files), the `&&` stops the
  run, so look for a missing log entry.
- The weekly plot runs Mondays at 01:05, five minutes after the daily one, so
  the two never check out the plotter at the same time. It plots the last 7
  windows.
- The two lines don't depend on each other: the plot reads yesterday's window
  and the submission writes today's.
- Check `logs/<campaign_name>/poms_auto_submit.log` daily for errors.
- Remove both entries when the campaign is done. Production access ends at the
  end of 2026.

## 4. How the submit script works

Each run it reads the stage's submissions from POMS and `condor_q`, decides
how many slices (0, 1 or 2) to launch, and launches them. A submission that
crosses `pct_complete_threshold` stops counting as in flight, freeing its slot.
The new slice takes the `pro` subgroup unless an in-flight submission already
holds it. When the Input Dataset is exhausted and the last slice has finished,
it builds a recovery slice for input files that never reached an output
dataset. The script never writes the ini; its own state (`last_split`, the
`switch` that cleanup turns off, caches) is the state file above.

- `max_splits` is the total number of launches an ini may make. With
  `max_splits = 1` and `last_split = 1`, re-running does nothing.
- To stop a campaign, stop its cron job. To re-run an ini that cleanup
  switched off, set `"switch": true` in its state file.
- Before every launch the script writes the stage's `cs_split_type` as
  `new(window=…,firsttime=<first_start + last_split*step>,…)` and resets
  `cs_last_split` to 0 (ADR-0023). Times are read as Central and sent as GMT.
  `round`, `fts`, `localtime` and `lasttime` are optional (defaults in
  `configs/config.ini`). A Test Launch does not set the window.

Design decisions are in `docs/adr/`; domain terms are in `CONTEXT.md`.

## 5. Output and test launches

A real run (`test_launch = 0`) tags files `data_SBND2026A_<campaign>_v10_14_02_08`:

- `data_SBND2026A_Auto_POMS_SBND2026A_Gen2_Run3_BeamLight_v10_14_02_08_{reco1,larcvreco1,histreco2,caf,flatcaf}_sbnd`

Files land in `${IFDH_OUTPUT_DIR}/<tier>/<stream>/<hash prefix>/` with
`IFDH_OUTPUT_DIR=<basedir>/data/SBND2026A/v10_14_02_08/<campaign>/`.

`test_launch = 1` uses the stage's `test_param_overrides` (`prodtype=test`,
subgroup `test`) and writes separate `test_SBND2026A_…` definitions. It does
not set the window: the stage's `test_split_type` is `new(window=1m, firsttime=<blank>)`,
so a test launch is not tied to the ini's `[split]`.

## 6. History

- Oct 23–29: 3h slices under `sbnd_beamlight_newsplit_test.ini` (`first_start`
  2026-10-25 04:00). The stage's windows ran up to 10-29 04:00 CDT.
- 2026-11-14: one 1h slice, Nov 13 00:00–01:00 CST, using `sbnd_beamlight_run3.ini`
  (submission 3181354, cluster `30771358@jobsub04.fnal.gov`, 16 files, subgroup
  `pro`). It was a launch check. A stray submission, 3181349, was removed with
  `jobsub_rm` right after it launched.
- **Not processed: Oct 30 – Nov 12.** Set `first_start` to where you want to
  resume. `step` and `window` should match, or slices will overlap or leave gaps.

## 7. Gotchas

- **Windows follow `last_split`, not the date.** If a day's launch doesn't
  happen (the previous slice is still in flight at 04:00, the checkout fails,
  or the host is down), the next run takes the skipped window and every later
  one lags by a day. The plotter picks windows by calendar date, so its labels
  stop matching what was submitted. Check `last_split` in the state file when
  a day is missed.
- **Slow slices.** One day is normally enough for a 3h slice to finish, so the
  plot's counts are final. A slow or held slice shows partial outputs.
- **One stuck POMS submission blocks all planning.** A submission left in
  `New`/`Idle` with no `condor_q` data counts as in flight at any age, and the
  script only WARNs after 5 runs (ADR-0017). 3175072 (`New`) did this until it
  became `LaunchFailed`. If the dry run says `submit 0 slice(s) (in_flight=1 …)`,
  list the submissions and cancel the stuck one in POMS.
- **A stage that has been inactive looks empty.** `show_campaign_stages` and
  `scripts show_stage.sh` returned 0 stages (stage not found) until a
  submission was made. Stage ids and submissions are still listed via
  `campaign_stage_submissions`.
- **Empty slices fail to launch** with `LaunchFailed`. Count the files first.
- **State file per ini.** Two inis on one stage do not share state (ADR-0020).
- **Stage overrides.** `-Oglobal.configuration=` is still unset, so
  `configuration.name` is `override_me` in the metadata. Check the stage
  overrides (`-Oglobal.campaign`, `project_name`, `version`) before the next
  launch. A cloned campaign override once put 325 files into the dev sample's
  definitions.
- **Plotter constants drift.** The plotter's hard-coded `first_start`, `step`
  and `window` are not read from the ini. Change one side without the other
  and the plot shows the wrong window.
