# Make the lock file configurable again, one per config

**Reverses** part of the "Run poms_auto_submit as sbndpro" restructure
(commit 7b6ceaf), which fixed the lock file at a single path,
`<repo root>/poms_auto_submit.lock`, shared by every run regardless of which
`config.ini` it was given.

## Why that was wrong

The repo is meant to run several campaign stages at once, each on its own
cron line pointing at its own config file (see `configs/detvar3.ini` as an
example, and CLAUDE.md's note that campaign-specific inis are untracked).
A single shared lock file means only one campaign stage's cron run can hold
the lock at a time — a slow run for one campaign stage (e.g. waiting on a
POMS call) blocks every other campaign stage's run for that hour, even
though they have nothing to do with each other. `acquire_lock()` exists to
stop *the same* campaign stage's runs from overlapping if one hangs past the
hour, not to serialize unrelated campaign stages against each other.

## The fix

`lock_file` is back in `config.ini`'s `[paths]` section, resolved relative
to the config file's directory exactly like `log_file` already is. Each
campaign stage's config now points at its own lock file, so cron runs for
different campaign stages never contend with each other.
