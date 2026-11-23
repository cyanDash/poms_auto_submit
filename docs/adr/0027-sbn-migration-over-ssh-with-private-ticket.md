# SBN migration runs over SSH with a private Kerberos ticket

## Context

Mirroring a finished campaign's Output Definitions to SBN SAM takes hours per
definition, so they run in parallel on different GPVMs. The GPVMs accept only
Kerberos logins, and ADR-0004 removed `kinit` from the cron after a bad cron
ticket interfered with the same user's login session.

## Decision

`scripts/migration.py` gets a ticket from sbndpro's existing keytab into a
private credential cache and destroys it at the end of the run, the method
`sbnd-data-web/copy_keepup_plot.sh` already uses. No `kcron` setup is added and
credentials are not forwarded, so no login session's ticket is touched. This
narrows ADR-0004: POMS and SAM still use managed tokens only.

The step walks sbndgpvm01 to 07, one definition per machine, and circles back
past any SSH or token failure until every definition is submitted. Each job is
detached in `screen`, and hourly runs only poll it.

- The token is minted over SSH before launch, because `screen -dm` reports
  success before the job runs.
- The job claims its definition with an atomic symlink on `/exp` naming its
  machine, so a launch retried after an ambiguous SSH failure cannot run twice.
- A claimed definition is never relaunched, because `migrate_definition.sh`
  refuses files already in SBN.
- Exit 0 is not trusted, because the script's post-checks only print errors.
  The log must be clean and the SBN file count must equal SBND's.

## Consequences

The real path cannot be tested as a personal user.
`scripts/migration_cron_probe.sh` checks it from sbndpro's cron without starting
anything. A failed definition keeps the switch on until a person resolves it.
