# Run as sbndpro with managed tokens, not kinit

The cron used to `kinit` with a dedicated per-user cron keytab (`kcroninit`, then `kinit -kt /path/to/cron.keytab <user>/cron/<host>@FNAL.GOV` before every run) so it could authenticate without an interactive login, since a normal `sbndpro` login was assumed off-limits for cron. In practice this backfired: a bad Kerberos ticket produced by that cron flow interfered with the same user's own interactive home-area access.

The fix is to run the script only as the `sbndpro` service account instead. `sbndpro` has managed tokens configured (`--credkey=sbndpro/managedtokens/fifeutilgpvm01.fnal.gov`), so `htgettoken` alone is enough to mint a bearer token — no `kinit`, no cron keytab, no separate Kerberos ticket to go stale and collide with anyone's login session.

This removes two things from `setup.sh`:
- The `--role <role>` argument and the `-r "$role"` passed to `htgettoken`. The credkey already pins the role to `production`, so there's nothing left to select at call time.
- The "refresh POMS's own copy of the vault token" step (`WEB_CONFIG` + `$POMS_CLIENT_DIR/bin/upload_file --vaulttoken ...`). Not necessary anymore.

Accepted consequence: the script — interactive or cron — must now run as `sbndpro`, the opposite of the old guidance that the cronjob couldn't run as `sbndpro`. Anyone without `sbndpro` access can no longer self-serve running it and needs to go through whoever holds that account.
