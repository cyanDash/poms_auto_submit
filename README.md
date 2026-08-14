# poms_auto_submit

An hourly cron script for SBND production: checks how far a POMS campaign
stage's Running submissions have progressed, decides how many new slices (0,
1, or 2) are ready to go out, optionally updates the stage's parameters, and
submits them via POMS. Every decision is written to a log file.

## What it does, each run

1. **Check progress** — resolves the campaign stage, and reads back
   status + `pct_complete` for every currently-`Running` submission (or the
   latest one, if none are running).
2. **Decide** — `can_submit_next_slice()` keeps a target number of slices in
   flight (1, or 2 if `submit_two_slices` is set). It submits enough new
   slices to top the pipeline back up to that target, counting a Running
   submission as done occupying its slot once its `pct_complete` crosses
   `pct_complete_threshold`. Returns 0 if nothing is ready yet.
3. **Read/update stage params** — fetches the current stage parameters, and
   applies any overrides you configure.
4. **Submit** — launches as many new slices as step 2 decided on.

`--dry-run` runs steps 1–3 and logs what step 3/4 *would* do, without calling
POMS to update params or submit anything.

## Setup

Requires a UPS environment with `poms_client` available on CVMFS.

```bash
git clone https://github.com/cyanDash/poms_auto_submit.git
cd poms_auto_submit
source setup.sh
```

## Configure

Copy/edit `config.ini`:

```ini
[poms]
experiment = sbnd
role = production
campaign_name = CHANGE_ME
campaign_stage_name = CHANGE_ME

[decision]
pct_complete_threshold = 80
submit_two_slices = 0

[paths]
log_file = poms_slice_cron.log
lock_file = poms_slice_cron.lock
```

Set `campaign_name`/`campaign_stage_name` to a campaign stage you own. This
always talks to production POMS.

## Example workflow

Validate against a real campaign before trusting it unattended:

```bash
source setup.sh
./poms_slice_cron.py --config config.ini --dry-run
```

Check `poms_slice_cron.log` and confirm the logged progress/status/decision
match what you'd expect for that stage's current state.

Once that looks right, run it for real once and confirm exactly one
submission goes out:

```bash
./poms_slice_cron.py --config config.ini
```

Then wire it into cron:

```cron
0 * * * * cd /path/to/poms_auto_submit && source setup.sh && ./poms_slice_cron.py --config config.ini >> cron.out 2>&1
```

## Tests

```bash
source setup.sh
pytest test/
```

Tests cover the progress-check, stage-param, and submit blocks against
fakes built from real `poms_client.py` response shapes — no network calls.
`test/verify_real_campaign.py` and `test/debug_raw_call.py` are separate,
read-only manual tools for checking live behavior against the real POMS
server (not part of the pytest suite).
