# poms_auto_submit

Cron script for SBND production: checks how far a POMS campaign
stage's Running submissions have progressed, decides how many new slices (0,
1, or 2) are ready to go out, and submits them via POMS. Every decision is
logged.

## What it does, each run

1. **Check progress** — reads back status + `pct_complete` for every
   currently-`Running` submission (or the latest one, if none are running).
2. **Decide how many** — a new slice goes out once the last run's slices'
   `pct_complete` crossed `pct_complete_threshold`, up to `submit_two_slices`
   at a time. Stops for good once `last_split` reaches `max_splits`;
   `last_split` is a counter the script maintains itself in `config.ini`.
3. **Decide subgroup, submit** — only the `production` role may hold the
   higher-priority `pro` subgroup. A lone slice always takes `pro` (nothing
   else from this run to contend with it); when 2 slices go out in the same
   run, one is `pro` and the other standard, since both can't hold it at
   once. Every other role always runs standard.

`--dry-run` logs what would happen without calling POMS to update params or submit anything.

## Setup

Requires a UPS environment with `poms_client` available on CVMFS.

```bash
git clone https://github.com/cyanDash/poms_auto_submit.git
cd poms_auto_submit
# Optional but good practice: make a new branch for running your specific campaign at this point
source setup.sh --role production
```

`--role` is passed through as htgettoken's `-r` flag (use your `config.ini`'s
`role`); omit it to fetch a token without one.

## Configure

Copy/edit `config.ini`:

```ini
[poms]
experiment = sbnd
role = production
campaign_name = override_me
campaign_stage_name = override_me

[decision]
; master on/off switch; when false, the script just logs and exits without
; checking progress or submitting. Useful in case you want to pause submitting
; new jobs but do not want to delete and rewrite the crontab
switch = 1

; what percentage of the total number of jobs must be completed before the
; next slice is submitted; 0-100
pct_complete_threshold = 70

; 0: keep 1 slice in flight at a time. 1: keep 2 slices in flight at a time.
submit_two_slices = 0

; total number of slices this campaign stage needs; submission stops once
; last_split reaches max_splits
max_splits = 5

; counter of slices successfully submitted so far
; updated by the script after each run, don't hand-edit while cron is active
last_split = 0

[paths]
; path to the log and log files
log_file = poms_auto_submit.log
lock_file = poms_auto_submit.lock
```

Set `campaign_name`/`campaign_stage_name` to a campaign stage you own. `submit_two_slices` = 1 implies a pro and a non-pro submission can be 
simultaneously run. `switch = 0` is a kill switch: the script just logs that
it's off and exits, without checking progress or submitting anything.

## Example workflow

Validate against a real campaign before trusting it unattended:

```bash
source setup.sh --role production
./poms_auto_submit.py --config config.ini --dry-run
```
A dry run fetches information about the currently active submissions and prints out
what it would do given this information. It does not submit a new slice, nor does it
update the parameters for a stage.

Check `poms_auto_submit.log` for the logged progress/status/decision, then run
for real once manually and confirm that the submission goes out:

```bash
./poms_auto_submit.py --config config.ini
```

Then wire it into cron with a dedicated keytab.

**NOTE**: The cronjob cannot be run as an `sbndpro` user. Login
with your own id instead. Ex. `ssh sdas1@sbndgpvm03.fnal.gov`

Cron doesn't inherit your
login session's Kerberos ticket. So generate one.

```bash
kcroninit  # generates /path/to/cron.keytab for <user>/cron/<host>@FNAL.GOV
```


Open the crontab:
```bash
crontab -e
```

And paste the following script:
```cron
SHELL=/bin/bash
0 * * * * kinit -kt /path/to/cron.keytab <user>/cron/<host>@FNAL.GOV && cd /path/to/poms_auto_submit && source setup.sh --role production && ./poms_auto_submit.py --config config.ini
```
Make appropriate changes for the file paths and the user/hostname. You now have a crontab installed that runs at the first minute of every hour. Make sure to delete the crontab at the end of your campaign.
