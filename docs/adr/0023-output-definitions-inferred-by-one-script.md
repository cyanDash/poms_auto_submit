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
`{name, depth}` entries. `get_output_definitions(cfg, session, sam)` returns
what the section holds, or infers and records it when empty. A flat list of
names left by older code is never trusted, by recovery or by cleanup: it
came from the unscoped discovery and can name other campaigns' definitions,
so it is inferred again and replaced. If that inference fails the flat list
is left as it is and an error is raised.

Two methods:

- **Postscript.** The newest Submission's `command_executed` names the
  metadata postscript (`-f dropbox://.../metadata_postscripts*.sh`) and
  carries the `export MT_*=` values and the version (`--mdappversion`, which
  the postscripts call `SBNDCODE_VERSION`). Without `--mdappversion` (the
  sbnnusyst reprocess, submission 3179008) the version is `MT_PROJECTVERSION`,
  else that of the `setup-unquote sbndcode <v>` (or `icaruscode`); step 1
  says which source was used. The postscript's `*DTAG*=`
  assignments, with known variables substituted and unknown ones left as
  wildcards, give the expected-name patterns. The script is read as text,
  never executed.
- **Lineage.** `Dataset.Tag` of the descendants, with depth, of the Input
  Dataset's files: all of them when it has at most 200 files, else up to 20
  inputs that have children (at most 200 probed). The dataset sampled is the
  original Input Dataset: after recovery the stage's dataset is
  `<input>_<campaign_name>_recovery_campaign`, and that suffix is stripped.

Names always come from lineage; the postscript only says which of them are
this stage's:

- Both available: a lineage name is recorded if it matches a pattern. A
  lineage name that carries the stage's production name and version but
  matches no pattern is a disagreement: nothing is recorded and
  `OutputDefinitionsError` is raised. A pattern with no file under it is an
  optional tier and is fine.
- Postscript unreadable (it is deleted when a production area is cleaned
  up): the lineage names carrying `<production name>_<version>` or
  `<MT_SAMPLE>_<version>` as one piece (leading `test_` ignored) are
  recorded, and the fallback is logged; step 6 shows the scope. The sample
  covers campaigns whose tags have no production name (sbnnusyst: `..._<sample>_<version>_flatcaf_sbnd`).
  The two must be next to each other, so a sibling campaign
  `PDS_detvar` does not take `PDS_detvar_Sample3_<version>` names, and
  `..._IntrinsicNuE_CV_v1_00_01_...` is not taken for `v1_01_00`.
- No launch command, or none of production name, sample and version found: nothing is recorded and
  `OutputDefinitionsError` is raised. Unscoped lineage is never used.

A dry run does not write the section.

Lineage tags that begin with `test_` or `test<N>_` are a Test Launch's outputs
under the same inputs (its `MT_PRODUCTIONNAME` is `test_...`). They are never
recorded and never count as a disagreement; step 4 of the trace lists them.

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
- The real SAM client comes from `sam_client.get_sam_client(experiment)`.
  It imports `samweb_client` on first use, so a filled section needs no SAM
  setup at all.
- Postscript patterns match the whole name (`fnmatchcase`), never a part of
  it.
- A campaign whose names put something between the production name and the
  version (the stream name in sbnutil's `metadata_postscripts.sh`) finds
  nothing by the fallback and raises. It needs its postscript.
- The disagreement check still uses the loose form (production name or
  sample, and version, each anywhere in the tag). It can only raise, so with the postscript present a
  sibling campaign sharing inputs and version is reported as a disagreement.
- A stage that produced no output at all cannot be inferred, so recovery
  retries each run instead of concluding, as before.
- `scripts/show_output_definitions.py --campaign <c> --stage <s>
  [--all-tags]` is the read-only lookup for an operator. It has no ini and
  no `state_file`, so it always infers. It prints the trace below as
  sections, then each definition's depth, tier and file count. `--all-tags`
  is kept and adds every lineage tag with the newest `create_date` seen
  among its sampled files and whether it was accepted, rejected (another
  campaign's) or ignored (a Test Launch tag). It writes nothing: no State
  File, no SAM or POMS write. With a failed inference it still prints the
  trace, then the error on stderr and exits 1.

## Trace of the inference

For debugging (operator request 2026-11-15), the inference logs at INFO the
candidates at every decision stage, in order, each line prefixed
`output definitions: step N <name>:` and listing the tags (with depth) the
stage keeps and the ones it drops with the reason. The steps are one data
structure built by the internal scan, shared by `get_output_definitions()`
(which logs them), `describe_lineage_tags()` and
`trace_output_definitions()` (the read-only view the lookup tool prints):

1. **launch command**: the Submission it came from (not a Test Launch, or
   the fallback newest of any kind), production name, version, postscript
   path and whether it was readable.
2. **input dataset**: the stage's dataset, the original one when a Recovery
   Dataset suffix was stripped, and how many inputs were probed and had
   children.
3. **lineage tags**: every tag found, with depth and number of sampled files.
4. **test launch tags**: tags dropped as `test_` / `test<N>_`.
5. **postscript patterns**: the patterns, or why there are none.
6. **classification**: with patterns, kept when a pattern matches; dropped
   as a disagreement (carries this stage's production name and version, no
   pattern) or as another campaign's (neither). Without them, the fallback
   scope string, the tags that carry it kept and the rest dropped.
7. **result**: the definitions recorded, or why nothing was recorded.

A launch command that is missing ends the trace after step 1 and step 7.
