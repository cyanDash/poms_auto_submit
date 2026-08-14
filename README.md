# poms_auto_submit

An hourly cron script for SBND production: checks how far a POMS campaign
stage's latest submission has progressed, decides (via a stub you fill in)
whether the next slice is ready to go out, optionally updates the stage's
parameters, and submits the next slice via POMS. Every decision is written to a log file.

## What it does, each run

1. **Check progress** — resolves the campaign stage, finds its latest
   submission, and reads back status + `pct_complete`.
2. **Decide** — `can_submit_next_slice()` in `poms_slice_cron.py` is an
   intentional stub (`# TODO(user)`). Fill in your own completion/threshold
   logic here; nothing downstream runs unless it returns `True`.
3. **Read/update stage params** — fetches the current stage parameters, and
   applies any overrides you configure.
4. **Submit** — launches the next slice's jobs if step 2 said yes.

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

Check `poms_slice_cron.log` and confirm the logged progress/status match
what the POMS web UI shows for that stage, then implement your real decision
logic in `can_submit_next_slice()`.

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
