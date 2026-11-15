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

## Cron

Only after a manual dry run and a manual real run look right. Leave the
checkout on `new-split-window`; switching branches under a running cron changes
the code it runs.

```cron
SHELL=/bin/bash
0 * * * * cd /exp/sbnd/app/users/sdas1/production_helper_scripts/poms_auto_submit && source setup.sh && ./scripts/poms_auto_submit.py -c configs/<ini>
```

Remove the cron entry when the campaign is done. Production access ends at
the end of 2026.
