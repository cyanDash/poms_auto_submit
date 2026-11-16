# sbnd-poms — Reference

## Gotchas

### Production vs analysis campaigns -- different tokens

SBND POMS has two kinds of campaign, gated by different access/tokens. The
scripts here only implement the **production** path. **Ask which kind a
campaign is before submitting/touching it** -- it's not guessable from the
campaign name.

- **Production**: token via `poms_auto_submit/setup.sh`'s managed-token
  credkey (`--credkey=sbndpro/managedtokens/fifeutilgpvm01.fnal.gov -r
  production`) -- what every script in this skill uses. Works fine as a
  regular user, usually silent.
- **Analysis**: token via
  `export BEARER_TOKEN_FILE=/tmp/bt_u$(id -u)` then
  `htgettoken -v -a htvaultprod.fnal.gov -i sbnd` (no managed credkey, SBND
  Analyser role, no manual verification needed). Not wired into any script
  here yet -- would need an analysis-token variant if analysis-campaign
  work comes up.
- **First time only, for a new analysis campaign** (outside the SL7
  container): `source /exp/sbnd/app/users/vito/products/setup_project_py_el9.sh`
  then `Project.py --upload_credentials`, to upload the vault token to the
  grid. Not needed for production campaigns, and not needed on every
  analysis submission.

If minting either token ever prompts for manual browser verification instead
of succeeding silently, **surface that URL to the user directly** and wait
-- don't retry blindly. Once they complete it, tokens can be fetched without
that step for about 3 days.

### POMS override values: whitespace / `${}` breaks the launch parser

POMS's launch-time argument construction pastes each `-O<section>.<key>=`
override's value into an **unquoted shell command line** server-side. Any of
the following in a value will make `fife_launch` fail with
`Error: extra command line arguments [...]` at *launch* time, not at cfg
parse time, which makes it look unrelated to the override that actually
caused it:

- A leading or trailing space (e.g. `" standard_reco1_sbnd_dnnroi_triton.fcl"`
  or `"standard_reco2_sbnd.fcl "`) — the space splits into an extra shell
  token. Hit this exact bug live via the POMS API path (`fclfile4`/`fclfile5`
  on campaign_stage_id 26625) — the error was
  `Error: extra command line arguments ['standard_reco1_sbnd_dnnroi_triton.fcl']`,
  which looked like a missing-quotes problem but was actually just a stray
  leading space in the stored override value.
- A second `=` inside the value.
- Unescaped `${...}` variable syntax — gets expanded/word-split before
  `fife_launch`'s own argument parser ever sees it.

`show_stage.py`/`show_stage.sh` flag the first two automatically.
`update_stage_params.py`/`.sh` refuse to *write* a bad value unless you pass
`--force`. If you ever bypass these scripts (POMS GUI param_overrides field,
a different API call), re-run `show_stage.sh` afterward to confirm nothing
stray landed.

Workaround discovered this session for the POMS **GUI** override field
specifically (not needed for the API path above): when a value needs a
literal space that the GUI's own pipeline mishandles (e.g.
`. ${CONDOR_DIR_INPUT}/script.sh`), typing literal single-quote characters
directly into the value field worked, since POMS pastes the text verbatim
into an unquoted shell command. Prefer avoiding the need entirely — bake
logic into a dropboxed script referenced by a single bare-token override —
over relying on this quoting trick.

### `set -u` / `nounset` vs UPS's `setups.sh`

`poms_auto_submit`'s `setup.sh` (and this skill's own scripts) source
`/cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh` and/or
`setup_sbnd.sh`. These reference unset variables internally (e.g.
`UPS_OVERRIDE`) and crash with `unbound variable` under `set -u`/`nounset`.
None of this skill's scripts use `-u` for this reason — only `set -eo
pipefail`. If you add a step that needs `-u`, enable it *after* any UPS
sourcing is done, never before.

### `jobsub_fetchlog` 401 Unauthorized

A personal token often lacks the `/sbnd/production` group needed to fetch
logs for a job that ran under the `sbndpro` production account (which is
every POMS-submitted job). `fetch_and_analyze.sh`'s fetch mode routes around
this by minting a bearer token via the same sbndpro managed-token credkey
`poms_auto_submit/setup.sh` uses (`--credkey=sbndpro/managedtokens/
fifeutilgpvm01.fnal.gov -r production`) — confirmed this session to work
fine run as a regular user, no need to be logged in as `sbndpro`
itself, and no need for the old workaround of fetching logs on a different
machine that happened to have production access.

A bare cluster id (`12345@jobsub03.fnal.gov`, no `.0`) makes landscape's
fetchlog ownership check fail with a misleading 401 even for a job you
genuinely own (`jobsub_q` will confirm you as OWNER). `fetch_and_analyze.sh`
normalizes this automatically by appending `.0` before the `@` if no
process-id dot is present.

A `KeyError: 'OTEL_EXPORTER_JAEGER_ENDPOINT'` traceback from `jobsub_lite`'s
tracing import right after running `jobsub_fetchlog` is harmless noise —
it prints "Continuing without tracing..." and the fetch still succeeds.

### DAG job log layout

For DAG-submitted (`dataset.dag`) jobs, `jobsub_fetchlog` first drops
`dagbegin`/`dagend`/`dagman_wrapper.sh`/`sambegin.*`/`samend.*` bookkeeping
files at the top level of the job dir — **not** the real `lar` stage logs.
The actual log is a `fife_wrap<timestamp><hash>cluster.<clusterid>.
<process>.{out,err}` pair, produced by a *separate* DAG node (a different
cluster id than the one you fetched with) that may not have logs yet if
that node is still running or hasn't started. `fetch_and_analyze.sh`
renames these to `<clusterid>.<process>.{out,err}` automatically (same as
the old `analyze_job.sh`) and reports plainly if none
were found yet, rather than treating that as an error.

### `poms_auto_submit`'s `submit_plan()` differs by branch

The `main` branch's `submit_plan()` only calls `set_subgroup()` +
`submit_next_slice()` per slice — no per-slice input-dataset handling
needed for a campaign stage with a real `cs_split_type` (e.g. `nfiles(10)`),
since POMS splits the input dataset itself. An older branch
(`icarus-fake-data-john`, kept for ICARUS work) additionally calls
`session.set_input_dataset()` every run, tied to `input_dataset_template` in
the `.ini` (`{n}` = `last_split`) — that's only correct for a campaign stage
with `cs_split_type: None` (manual pre-built per-slice datasets, see that
branch's ADR-0014). Check `git branch` in the repo and match the config
shape to the branch, or just checkout `main` unless you specifically need
that ICARUS workflow.

### Never `source` these scripts

Always invoke with `bash script.sh ...` or `./script.sh ...`, never
`source script.sh ...`. A prior sourced run that ever dies mid-`setups.sh`
can leave that shell with stale/partial UPS state (`$SETUPS_FILE`,
`$PRODUCTS`, etc.); later sourcing of even a *fixed* script in that same
shell can then silently fail to find `jobsub_fetchlog`/`poms_client` — no
error, just `command not found` — even though the script works fine in a
fresh shell. This was already confirmed the hard way for the old `analyze_job.sh`,
`analyze_job.sh`; the same risk applies here.

## Where things live

- `poms_auto_submit` repo (the repo root, four levels up from this skill's
  `scripts/`; the scripts find it from their own location) — its own
  `README.md`, `CONTEXT.md`, and `docs/adr/*.md` cover recovery, cleanup,
  stragglers, concurrency caps, and cron operation in more depth than this
  skill needs.
- `run-analyze-job` was merged into this skill (its `analyze_job.sh` is
  `fetch_and_analyze.sh --personal-token`).
- Domain-specific SBND-Triton-DNN-ROI gotchas (fcl pairing between a
  DNN-ROI-only detsim and its matching reco1 fcl; WIRECELL_PATH shadowing
  breaking jsonnet sibling imports): documented in the sibling repo
  `../triton_sbnd/grid/` and
  `../triton_sbnd/docs/cvn-triton-handoff.md` and
  neighboring files — not duplicated here since they're specific to that
  branch's physics config, not to POMS/submission mechanics.
