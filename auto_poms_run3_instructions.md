# Handoff: Run 3 BeamLight campaign (state as of 2026-11-14)

Run `poms_auto_submit` for `Auto_POMS_SBND2026A_Gen2_Run3_BeamLight`, stage
`decode_reco1_reco2_caf`, as `sbndpro`. README.md covers the general setup;
this file covers what is specific to this campaign.

| | |
|---|---|
| campaign_id / campaign_stage_id | 11649 / 27154 |
| Input dataset (stage `dataset`) | `run3_raw_data_ongoing` (grows with new files) |
| Split type | `new()` time window, driven by the ini's `[split]` section |
| Code branch | **`new-split-window`** (not on `main`; `main` is merged into it as of 6622267) |
| Output base dir | `/pnfs/sbnd/scratch/users/sbndpro/poms_production/` (set in POMS `param_overrides` and `test_param_overrides`; not in the ini) |

## Run it

```bash
cd /exp/sbnd/app/users/sdas1/production_helper_scripts/poms_auto_submit
git checkout new-split-window      # the [split] code lives only here
source setup.sh                    # as sbndpro; mints the production token
./scripts/poms_auto_submit.py -c configs/<ini> --dry-run
./scripts/poms_auto_submit.py -c configs/<ini> --follow-launch
```

`--follow-launch` prints POMS's launch output until the jobsub id appears.
Inis are gitignored, so copy your own. `configs/sbnd_beamlight_run3.ini` is the
reference:

```ini
[poms]
experiment = sbnd
campaign_name = Auto_POMS_SBND2026A_Gen2_Run3_BeamLight
campaign_stage_name = decode_reco1_reco2_caf

[decision]
pct_complete_threshold = 0
submit_two_slices = 0
max_splits = 1
test_launch = 0

[split]
first_start = 2026-11-13 00:00   ; Central time, start of the first slice
step = 1d                        ; first_start + last_split * step
window = 1h                      ; length of each slice
```

### `[split]`

- Before every launch the script writes the stage's `cs_split_type` as
  `new(window=…,firsttime=<first_start + last_split*step>,…)` and resets
  `cs_last_split` to 0 (ADR-0023). Times are read as Central and sent as GMT.
- `last_split` lives in `logs/Auto_POMS_SBND2026A_Gen2_Run3_BeamLight/state_<ini name>.json`.
  Slice number n uses `first_start + n*step`.
- `max_splits` is the total number of launches an ini may make. With
  `max_splits = 1` and `last_split = 1`, re-running does nothing.
- Preview a slice's size before launching. Convert the window to GMT and run
  `samweb count-files "defname: run3_raw_data_ongoing and create_date > '<start Z>' and create_date <= '<end Z>'"`.
  The Nov 13 00:00–01:00 CST slice holds 16 files; a 3h window holds roughly 26–40.

## Done so far

- Oct 23–29: 3h slices under `sbnd_beamlight_newsplit_test.ini` (`first_start`
  2026-10-25 04:00). The stage's windows ran up to 10-29 04:00 CDT.
- 2026-11-14: one 1h slice, Nov 13 00:00–01:00 CST, using `sbnd_beamlight_run3.ini`
  (submission 3181354, cluster `30771358@jobsub04.fnal.gov`, 16 files, subgroup
  `pro`). It was a launch check. A stray submission, 3181349, was removed with
  `jobsub_rm` right after it launched.
- **Not processed: Oct 30 – Nov 12.** Set `first_start` to where you want to
  resume. `step` and `window` should match, or slices will overlap or leave gaps.

## SAM output definitions

A real run (`test_launch = 0`) tags files `data_SBND2026A_<campaign>_v10_14_02_08`:

- `data_SBND2026A_Auto_POMS_SBND2026A_Gen2_Run3_BeamLight_v10_14_02_08_{reco1,larcvreco1,histreco2,caf,flatcaf}_sbnd`

Each held 121 files before the 2026-11-14 slice (expect 137 after it).
Files land in `${IFDH_OUTPUT_DIR}/<tier>/<stream>/<hash prefix>/` with
`IFDH_OUTPUT_DIR=<basedir>/data/SBND2026A/v10_14_02_08/<campaign>/`.

`test_launch = 1` uses the stage's `test_param_overrides` (`prodtype=test`,
subgroup `test`). It writes separate `test_SBND2026A_…` definitions. It does
not set the window: the stage's `test_split_type` is `new(window=1m, firsttime=<blank>)`,
so a test launch is not tied to the ini's `[split]`.

`-Oglobal.configuration=` is still unset, so `configuration.name` is
`override_me` in the metadata. Check the stage overrides (`-Oglobal.campaign`,
`project_name`, `version`) before the next launch. A cloned campaign override
once put 325 files into the dev sample's definitions.

## Gotchas hit on this campaign

- **A stage that has been inactive looks empty.** `show_campaign_stages` and
  `scripts show_stage.sh` returned 0 stages (stage not found) until a submission
  was made. Stage ids and submissions are still listed via
  `campaign_stage_submissions`.
- **One stuck POMS submission blocks all planning.** A submission left in
  `New`/`Idle` with no `condor_q` data counts as in flight at any age, and the
  script only WARNs after 5 runs (ADR-0017). 3175072 (`New`) did this until it
  became `LaunchFailed`. If the dry run says `submit 0 slice(s) (in_flight=1 …)`,
  list the submissions and cancel the stuck one in POMS.
- **State file per ini.** A new ini starts with `last_split = 0` and an empty
  cache; two inis on one stage do not share state (ADR-0020).
- **Empty slices fail to launch.** If a window has no files, the launch fails
  with `LaunchFailed`. Count the files first.
- `jobsub_rm -G sbnd --jobid=<id>` (after `source setup.sh`) removes a submitted
  cluster.

## Daily cron: submit at 04:00, plot at 01:00

Only after a manual dry run and a manual real run look right. Cron times are
the host's local time (Central).

**Submission, 04:00 every day.** The run at 04:00 on date D takes the files
created that day between 00:00 and 03:00, so the ini needs:

```ini
[decision]
max_splits = 60                  ; one launch per day, so this is the number of days to run
test_launch = 0

[split]
first_start = 2026-11-15 00:00   ; the first run is at 04:00 on this date
step = 1d
window = 3h
```

Slice n is `first_start + n*1d`, so the first run, at 04:00 on 2026-11-15,
submits 2026-11-15 00:00–03:00, and `last_split` must be 0 for it. The cron
line checks out `new-split-window` first, so a stray checkout of another
branch can't change the code it runs. If the checkout fails (uncommitted edits
to tracked files), the `&&` stops the run, so look for a missing log entry.

**Plot, 01:00 every day, starting one day after the first submission.** The
plotter lives in
`/exp/sbnd/app/users/sdas1/production_helper_scripts/campaign_progress_plotter`
on branch `auto-run3-data`. `scripts/window_output_plotter.py daily` plots
yesterday's window: for each of the five `data_SBND2026A_…` tiers, the output
file count against the input count for the window that started at 00:00 the
day before. So the 01:00 run on Nov 16 plots Nov 15 00:00–03:00, the slice
submitted at 04:00 on Nov 15, which has had 21 hours to finish. Starting the
plot cron a day after the submission cron is what gives it something to plot.
`weekly` plots the last 7 windows instead, and an optional date argument
replaces "today". The date comes from the system clock, so a missed day
doesn't shift the plot.

- The constants at the top of `scripts/window_output_plotter.py` are
  hard-coded and **must match the ini**: `first_start` (its time of day sets
  the plotted window; it is now `2026-11-15 00:00`), `step` (1d), `window`
  (3h), plus `input_dataset`, `campaign`, `version` and `tiers`. Edit and
  commit them on that branch.
- Output is `plots/<campaign>_<date>_daily.pdf`, relative to the current
  directory and gitignored, so the cron line must `cd` into the plotter first.
- Before the first full window has passed, the script exits with "No window
  has started before <date>; nothing to plot."
- It only reads SAM. `samweb count-files` works without a token (I checked
  with no bearer token set).

```cron
SHELL=/bin/bash
0 4 * * * cd /exp/sbnd/app/users/sdas1/production_helper_scripts/poms_auto_submit && git checkout -q new-split-window && source setup.sh && ./scripts/poms_auto_submit.py -c configs/<ini>
0 1 * * * cd /exp/sbnd/app/users/sdas1/production_helper_scripts/campaign_progress_plotter && git checkout -q auto-run3-data && source setup.sh && python scripts/window_output_plotter.py daily
```

Install the plotter line on 2026-11-16 (or let it print "nothing to plot" on
Nov 15). The two lines don't depend on each other: the plot reads yesterday's
window and the submission writes today's.

**Caveats**
- **Windows follow `last_split`, not the date.** If a day's launch doesn't
  happen (the previous slice is still in flight at 04:00, the checkout fails,
  or the host is down), the next run takes the skipped window and every later
  one lags by a day. The plotter picks windows by calendar date, so its labels
  stop matching what was submitted. Check `last_split` in the state file when a
  day is missed.
- One day is enough for the jobs of a 3h slice to finish, so the counts the
  plot reads are normally final. A slow or held slice shows partial outputs.

Remove both cron entries when the campaign is done. Production access ends at
the end of 2026.
