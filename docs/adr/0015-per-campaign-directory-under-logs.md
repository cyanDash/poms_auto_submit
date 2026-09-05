# Derive log/lock/cache paths from a per-campaign directory under logs/

**Reverses** the `[paths]` section introduced by
`0006-lock-file-configurable-per-config.md`: `log_file`/`lock_file` are no
longer set in `config.ini` at all.

## Why that was wrong

Every campaign-related file on disk (the log, the lock file, the
`submission_cache_<stage id>.json` from `0008-cache-static-submission-fields.md`,
`output_definitions_<stage id>.txt` from recovery) was scattered: some named
in `config.ini`'s `[paths]` section, some derived from `log_file`'s
directory, some living at the repo root (`<repo root>/<name>.lock`). Adding a
new campaign stage meant remembering to invent unique names for all of these
by hand across several files, and nothing stopped two configs from
accidentally colliding on the same lock or log file (exactly the bug
`0006` fixed once already, for lock files specifically).

## The fix

`load_config()` derives one directory, `logs/<campaign_name>/`, straight
from `campaign_name` (already required to be unique per running config) and
creates it if missing. Every campaign-related file — `poms_auto_submit.log`,
`poms_auto_submit.lock`, `submission_cache_<stage id>.json`,
`output_definitions_<stage id>.txt` — gets a fixed, generic name inside that
directory; the directory itself provides the per-campaign uniqueness that
`[paths]` used to provide by hand. `config.ini` no longer has a `[paths]`
section at all — nothing to configure, nothing to collide.

The whole `logs/` directory is `.gitignore`d (previously only `*.log`,
`*.lock`, `*.json`, and `output_definitions_*.txt` were, individually).
