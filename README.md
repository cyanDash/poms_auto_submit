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
3. **Decide subgroup, submit** — only the `production` role may hold the
   higher-priority `pro` subgroup, and only one slice at a time; every other
   role, and every other concurrent slice, runs at the standard subgroup.
   Reads the stage's current `param_overrides` to see whether `pro` is
   already in use, then for each new slice sets (or deletes) the
   `-Osubmit.subgroup=` override accordingly before launching it. When 2
   slices go out in the same run, one is submitted `pro` and the other
   standard (production role only).

`--dry-run` runs steps 1–2 and logs what step 3 *would* do, without calling
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
log_file = poms_auto_submit.log
lock_file = poms_auto_submit.lock
```

Set `campaign_name`/`campaign_stage_name` to a campaign stage you own. This
always talks to production POMS.

## Example workflow

Validate against a real campaign before trusting it unattended:

```bash
source setup.sh
./poms_auto_submit.py --config config.ini --dry-run
```

Check `poms_auto_submit.log` and confirm the logged progress/status/decision
match what you'd expect for that stage's current state.

Once that looks right, run it for real once and confirm exactly one
submission goes out:

```bash
./poms_auto_submit.py --config config.ini
```

Then wire it into cron:

```cron
0 * * * * cd /path/to/poms_auto_submit && source setup.sh && ./poms_auto_submit.py --config config.ini >> cron.out 2>&1
```

## Tests

```bash
source setup.sh
pytest test/
```

Tests cover the progress-check, stage-param, and submit blocks against
fakes built from real `poms_client.py` response shapes — no network calls.

`test/test_live_campaign.py` is a read-only regression suite against a real
campaign stage — resolving stage ids, `get_progress()`, `get_stage_params()`,
etc. against the actual server, to catch fake/reality drift the unit tests
above can't. It's marked `live` and excluded by default; run it explicitly:

```bash
pytest test/ -m live
```

`test/debug_raw_call.py` is a separate one-off manual tool for making raw
POST calls and inspecting the real response body (not part of the pytest
suite).
