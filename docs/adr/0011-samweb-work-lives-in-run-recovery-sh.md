# All samweb work lives in run_recovery.sh, not a Python module

## The problem

TODO.md item 1's steps 1-6 are all `samweb` CLI calls (list-definition-files,
file-lineage, get-metadata, create-definition, prestage-dataset). The first
attempt at this wrapped each one in a Python module,
`scripts/samweb_client.py`, mirroring `condor_progress.py`'s
subprocess-wrapping pattern. That module name collides with a real Python
package: `setup sam_web_client` (needed for the `samweb` CLI itself) puts an
actual `samweb_client` package on `$PYTHONPATH` (confirmed:
`/cvmfs/.../sam_web_client/v3_6/NULL/python/samweb_client/__init__.py`) —
`scripts/samweb_client.py` would shadow it.

## The fix

`scripts/run_recovery.sh` (bash) owns TODO.md's steps 1-6 end to end: it
calls `samweb` directly (`list-definition-files`, `file-lineage children`,
`get-metadata`, `create-definition`, `count-definition-files`,
`prestage-dataset`), builds the recovery dimension/dataset name, makes the
ratio decision, and prestages a data campaign's recovery dataset detached
(`setsid`+`disown`, so it survives past `poms_auto_submit.py`'s
per-campaign-stage lock — see ADR-0006). `scripts/recovery.py` only calls
this script (`subprocess.run`) and parses its three-line stdout contract
(ratio, threshold, then the new dataset name or the literal
`NO_RECOVERY_NEEDED`); everything
POMS-specific (checking the last slice's status, calling
`PomsSession.set_recovery_input_dataset()`/`submit_next_slice()`, persisting
`last_split`/`recovery_handled`) stays in Python, since `poms_client` has no
CLI/bash equivalent.

This also keeps closer to `setup_recovery.sh`'s original shape (the thing
being automated already was a bash script) and reuses `count-definition-files`
(a direct integer, simpler than parsing `list-definition-files --summary`'s
`Key:\tvalue` block).

## Recovery dataset is created before the ratio decision, not after

TODO.md's literal order counts each output dataset separately and only
creates the recovery dataset once the ratio decision says it's needed. That
requires combining multiple output-dataset file counts into one number
before deciding — ambiguous when the input feeds more than one output
dataset (confirmed live: `aurora_..._reco1_sbnd` alone feeds ~40 distinct
downstream `Dataset.Tag` values). Instead, the script builds the `not
isparentof: (A and B and ...)` recovery dimension and creates that
definition as soon as the output dataset names are known, then derives the
ratio from `count-definition-files` on the recovery dataset itself
(`output_count = input_count - recovery_count`) — letting SAM's own query do
the set combination instead of reimplementing it in bash.

`and`, not `or`, inside the parens: we want files missing *any* of their
expected outputs, i.e. `not(isparentof A and isparentof B and ...)`, which
is `not A or not B or ...` by De Morgan — a file recovers if it's short even
one output, not only if it produced none of them.

Accepted cost: on the "no recovery needed" path, an unused SAM definition is
left behind. Judged cheap — creating a definition has no prestage/launch
side effect attached to it alone.

## Probe file is scanned for, not assumed to be files[0]

TODO.md's step 1/2 pseudocode (`list-definition-files | head -n 1`, then
`file-lineage` on that one file) assumes the first file in the list has
output lineage. Not guaranteed — a file can have no children if it was never
run or failed outright, which is exactly the condition this feature exists
to find and fix. The script instead scans `list-definition-files`' output in
order and uses the first file whose `file-lineage children` comes back
non-empty, both as the campaign-type probe and as the source of output
`Dataset.Tag`s. Unbounded rather than capped: `recovery.py`'s
`RECOVERY_SCRIPT_TIMEOUT_SECONDS` already bounds the worst case (treated as
a retryable failure, not a hang), and by the time this script runs the
campaign stage's last slice has completed, so most files are expected to
have lineage already.
