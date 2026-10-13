# Output definitions are inferred by one script, from the postscript and lineage

## Context

`run_recovery.sh` found a Campaign Stage's output definitions by taking the
`Dataset.Tag` of every child of the first input file that had children, and
`recovery.py` copied them into the State File for cleanup to use.

Two faults:

- **Inputs are shared between campaigns.** Checked live: one input of the PDS
  detvar campaign has 16 children from four campaigns
  (`docs/raw/sam_lineage.json`). Lineage alone cannot say which definitions
  belong to this Campaign Stage, so recovery asked for outputs of other
  campaigns and cleanup would have retired their files.
- **Only a recovery run recorded them.** State is per ini (ADR-0020), so a
  campaign that finished first time had nothing for cleanup to read.

## Decision

`scripts/output_definitions.py` infers the definitions and is the only writer
of the State File's `output_definitions` section, which now holds
`{name, depth}` entries (a flat list of names left by older code is read as
depth 1). `get_output_definitions(cfg, session, sam)` returns what the
section holds, or infers and records it when empty.

Two methods:

- **Postscript.** The newest Submission's `command_executed` names the
  metadata postscript (`-f dropbox://.../metadata_postscripts*.sh`) and
  carries the `export MT_*=` values and the version (`--mdappversion`, which
  the postscripts call `SBNDCODE_VERSION`). The postscript's `*DTAG*=`
  assignments, with known variables substituted and unknown ones left as
  wildcards, give the expected-name patterns. The script is read as text,
  never executed.
- **Lineage.** `Dataset.Tag` of the descendants, with depth, of up to 20
  inputs that have children.

Names always come from lineage; the postscript only says which of them are
this stage's:

- Both available: a lineage name is recorded if it matches a pattern. A
  lineage name that carries the stage's production name and version but
  matches no pattern is a disagreement: nothing is recorded and
  `OutputDefinitionsError` is raised. A pattern with no file under it is an
  optional tier and is fine.
- Postscript unreadable (it is deleted when a production area is cleaned
  up): the lineage names carrying the launch command's production name
  (leading `test_` ignored) and version are recorded, and the fallback is
  logged.
- No launch command, or no name found: nothing is recorded and
  `OutputDefinitionsError` is raised. Unscoped lineage is never used.

A Test Launch never writes the section.

Recovery asks this script for the names and passes the depth-one ones to
`run_recovery.sh` as arguments; the shell script no longer discovers them
and `recovery.py` no longer writes the section. A failure to infer returns
`output_definitions_failed`, which is retried next run and does not mark
recovery handled.

## Consequences

- This partly reverses ADR-0011: definition discovery is SAM work done in
  Python through `samweb_client`, with the client injected so tests use a
  fake. The recovery dataset, counts and prestage stay in `run_recovery.sh`.
- The prestage log moves from `output_definitions_<id>_prestage.log` to
  `recovery_<id>_prestage.log` in the campaign directory.
- The launch command comes from the newest Submission that is not a Test
  Launch: a Test Launch runs with `test_param_overrides`, whose `MT_*`
  values can differ from the real ones.
- The disagreement check uses the production name as exported, `test_`
  included, so a test campaign sharing inputs and version with its real twin
  does not disagree with the twin's names. The fallback ignores the prefix
  and so cannot tell the two apart: it records both campaigns' names.
- The real SAM client is `output_definitions.LazySamClient(experiment)`,
  which imports `samweb_client` on first use, so a filled section needs no
  SAM setup at all.
- After a recovery run the stage's Input Dataset is the Recovery Dataset, so
  an inference done only then samples the recovery subset. In practice the
  section is filled when recovery is first evaluated, before the switch.
- A stage that produced no output at all cannot be inferred, so recovery
  retries each run instead of concluding, as before.

## Update (2026-10-13): cleanup does not trust a recorded flat list

"A flat list of names left by older code is read as depth 1" holds for recovery only. A flat list
came from the unscoped discovery and can name other campaigns' definitions. Cleanup calls
`get_output_definitions(..., trust_flat_list=False)`, which infers again and replaces the section
with `{name, depth}` entries. If that inference fails the flat list is left as it is and cleanup
does not run (ADR-0021).
