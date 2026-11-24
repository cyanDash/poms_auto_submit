# Run 3 BeamLight campaign: operating manual

Runs `poms_auto_submit` for `Auto_POMS_SBND2026A_Gen2_Run3_BeamLight`, stage
`decode_reco1_reco2_caf`, as `sbndpro`, and plots the daily and weekly output
counts (done by another repo). Four cron jobs (host local time, Central):
submit at 04:00, recovery at 18:00, daily plot at 10:00, weekly plot at 10:10.

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
6. Install the cron lines (section 3 below), starting 2026-11-15.

## 2. Plot

The plots come from a separate repo (see the table). Before starting, open
`scripts/window_output_plotter.py` there and set `first_start`, `step` and
`window` to the same values as the ini, and `campaign`, `input_dataset`,
`version`, `tiers`, `output_directory` and `state_name` (`state_<ini name>.json`)
to your campaign's. Commit them on `auto-run3-data`.

Try it by hand, then check the PDF in the plots directory:

```bash
cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/campaign_progress_plotter
git checkout auto-run3-data
source setup.sh
python scripts/window_output_plotter.py daily
```

Before the first full window has passed it prints "nothing to plot". Install
the cron lines (section 3) on the day after your first slice.

## 3. Cron

Only after a manual dry run, a manual real run and a cron rehearsal look right.

**Rehearse each line under cron's environment.** Cron runs lines with `/bin/sh`,
`PATH=/usr/bin:/bin` and no login profile, so a line that works in your shell can
still fail there (`source setup.sh` and `condor_q` both did). Rehearse as
`sbndpro`, with `--dry-run` on the submit line. The plot line writes the real PDF:

```bash
env -i SHELL=/bin/sh PATH=/usr/bin:/bin HOME=/exp/sbnd/app/home/sbnd LOGNAME=sbndpro USER=sbndpro \
  /bin/sh -c '<the line from the cron block, after the five time fields>'
```

The rehearsal passes when it prints what the manual run printed.

The four lines run in this order. Each day's window is the 3 hours of input
files created from 00:00 to 03:00 that day:

```
  Day 1                                   Day 2
  -----                                   -----
  04:00  submit Day 1 window              04:00  submit Day 2 window
           |
  18:00  recover Day 1 inputs             10:00  plot Day 1 window (daily)
         that have no output  ---------->  10:10  plot last 7 windows (weekly)

                                          18:00  recover Day 2 inputs ...
```

The plots always show the previous day's window, so they start the morning
after the first submission.

Then open `crontab -e` and paste:

```cron
0 4 * * * cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/poms_auto_submit && git checkout -q new-split-window && source ./setup.sh && ./scripts/poms_auto_submit.py -c configs/<ini>
0 18 * * * cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/poms_auto_submit && git checkout -q new-split-window && source ./setup.sh && ./scripts/window_recovery.py -c configs/<ini>
0 10 * * * cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/campaign_progress_plotter && git checkout -q auto-run3-data && source ./setup.sh && python scripts/window_output_plotter.py daily
10 10 * * * cd /exp/sbnd/app/users/sbndpro/mcp/sdas1/campaign_progress_plotter && git checkout -q auto-run3-data && source ./setup.sh && python scripts/window_output_plotter.py weekly
```

- Use `source ./setup.sh`, not `source setup.sh`. Cron runs lines with
  `/bin/sh` unless the crontab sets `SHELL=`, and in that mode `source` searches
  `PATH` for a bare name and fails with `setup.sh: file not found`.
- The checkout in each line stops a stray branch from changing the code that
  runs. If it fails (uncommitted edits to tracked files), the `&&` stops the
  run, so look for a missing log entry.
- The weekly plot runs ten minutes after the daily one, so the two never check
  out the plotter at the same time.
- Run the plots before any job that reads the plots directory (the data
  keepup cron).
- The 18:00 recovery shares the submission's lock, so the two never run at once.
- Check `logs/<campaign_name>/poms_auto_submit.log` daily for errors.
- Remove both entries when the campaign is done. Production access ends at the
  end of 2026.

## 4. Changing a run

- `max_splits` is the total number of launches an ini may make. With
  `max_splits = 1` and `last_split = 1`, re-running does nothing.
- To stop a campaign, remove its cron lines. To re-run an ini that cleanup
  switched off, set `"switch": true` in its state file.
- To recover a window again, delete its entry under `window_recovery` in the
  state file.

### Recovery cron (18:00)

`scripts/window_recovery.py -c configs/<ini>` re-launches the inputs of the
latest window that have no output. Before its first run, set by hand in POMS:

- `test_split_type` to None;
- every `test_param_overrides` entry other than the subgroup and memory equal
  to `param_overrides` (remove `-Oglobal.prodtype=test`).

The script refuses to launch otherwise and logs which entries differ. While the
stage is set up this way, a hand-made Test Launch also writes real outputs.
`--dry-run` shows what it would recover.

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

- 2026-11-15 04:17: first slice, Nov 15 00:00–03:00 CST, launched by hand with
  `sbnd_beamlight_run3.ini` (submission 3181441).
- 2026-11-15 19:02: first window recovery. Its Test Launch (3181703) failed
  with an empty dataset; 3181708 recovered the window.
- 2026-11-16: the 04:00 cron submitted Nov 16 00:00–03:00 (3181853) and the
  16:00 cron recovered it (3182055). The recovery cron has since moved to 18:00.
- 2026-11-17: the daily and weekly plots run at 10:00 and 10:10 every day.

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
- **Empty windows are skipped, not launched** (ADR-0027): the script counts the window's files in SAM first and moves `last_split` on without calling POMS. Before that fix (2026-11-19..24) an empty launch left a Submission in `New` and the run waiting on it for days, holding the lock.
- **State file per ini.** Two inis on one stage do not share state (ADR-0020).
- **Stage overrides.** `-Oglobal.configuration=` is still unset, so
  `configuration.name` is `override_me` in the metadata. Check the stage
  overrides (`-Oglobal.campaign`, `project_name`, `version`) before the next
  launch. A cloned campaign override once put 325 files into the dev sample's
  definitions.
- **Plotter constants drift.** The plotter's hard-coded `first_start`, `step`,
  `window` and `state_name` are not read from the ini. Change one side without
  the other and the plot shows the wrong window.
