# poms_auto_submit

A script for SBND production: checks how far a POMS campaign
stage's Running submissions have progressed, decides how many new slices (0,
1, or 2) are ready to go out, optionally updates the stage's parameters, and
submits them via POMS. Every decision is written to a log file. This script
can be run as an hourly cronjob.

## What it does, each run

1. **Check progress** — resolves the campaign stage, and reads back
   status + `pct_complete` (percentage of the total jobs that have
   finished) for every currently-`Running` submission (or the
   latest one, if none are running).
2. **Decide** — `next_slice_count()` how many new slices to run.
   A new slice is ready to be submitted to the last run slices'
   `pct_complete` have crossed `pct_complete_threshold`. Returns 0 
   if nothing is ready yet. Submission stops for good once `last_split`
   reaches `max_splits` — `last_split` is a counter the script maintains
   itself, incrementing it and writing it back to `config.ini` after each
   slice it successfully submits.
3. **Decide subgroup, submit** — only the `production` role may hold the
   higher-priority `pro` subgroup, and only one pro slice at a time; every
   other role, and every other concurrent slice, runs at the standard subgroup.
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
git checkout v1.0
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
max_splits = 5
last_split = 0

[paths]
log_file = poms_auto_submit.log
lock_file = poms_auto_submit.lock
```

Set `campaign_name`/`campaign_stage_name` to a campaign stage you own. Hopefully
this is all you need to configure.

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

Then wire it into cron. Cron doesn't inherit your login session's Kerberos
ticket, so `kinit` with a dedicated cron keytab/principal
(`<user>/cron/<host>@FNAL.GOV`)

The keytab can be generated with this command.

```bash
kcroninit
```
And in the crontab:

```cron
SHELL=/bin/bash
0 * * * * kinit -kt /path/to/cron.keytab <user>/cron/<host>@FNAL.GOV && cd /path/to/poms_auto_submit && source setup.sh && ./poms_auto_submit.py --config config.ini >> cron.out 2>&1
```
