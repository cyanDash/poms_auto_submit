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
  metadata postscript (`-f dropbox://.../metadata_postscripts*.sh`). It is
  the command `fife_launch` built after applying the cfg and every
  `param_overrides` entry, so a `submit.f_N` or postscript line changed
  through an override is already in it. When several postscripts are
  shipped, the one the job runs (`. ${CONDOR_DIR_INPUT}/<name>.sh` in
  `--postscript-unquote`) is used; else the first one shipped. The command
  also
  carries the `export MT_*=` values and the version (`--mdappversion`, which
  the postscripts call `SBNDCODE_VERSION`). Without `--mdappversion` (the
  sbnnusyst reprocess, submission 3179008) the version is `MT_PROJECTVERSION`,
  else that of the `setup-unquote sbndcode <v>` (or `icaruscode`); step 1
  says which source was used. The postscript's `*DTAG*=`
  assignments, with known variables substituted and unknown ones left as
  wildcards, give the expected-name patterns. The script is read as text,
  never executed.
- **Lineage.** `Dataset.Tag` of the descendants, with depth, of the Input
  Dataset's files: all of them when it has at most 200 files, else every
  k-th input (k = size // 200, so the sample spans the whole dataset) until
  20 inputs with children were read. The dataset sampled is the
  original Input Dataset: after recovery the stage's dataset is
  `<input>_<campaign_name>_recovery_campaign`, and that suffix is stripped.

With a readable postscript the names come first and lineage verifies them
(amended 2026-11-15, see Name first below); without it the names come from
lineage, scoped by the production name or sample:

- Postscript readable: the candidates are the patterns with no wildcard
  (the name itself), the SAM definitions matching a wildcard pattern
  (`listDefinitions(defname=<pattern, * as %>)`) and the lineage tags
  matching a pattern. A candidate is recorded when it is verified (below).
  A lineage name that carries the stage's production name and version but
  matches no pattern is a disagreement: nothing is recorded and
  `OutputDefinitionsError` is raised. A pattern with no file under it is an
  optional tier and is fine.
- Postscript unreadable (it is deleted when a production area is cleaned
  up): the lineage names carrying `<production name>_<version>` or
  `<MT_SAMPLE>_<version>` as one piece (leading `test_` ignored) are
  recorded, and the fallback is logged; step 8 shows the scope. The sample
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

## Name first, then verify (2026-11-15)

Live evidence: stage `decode_reco1_reco2_caf` of
`Auto_POMS_SBND2026A_Gen2_Run3_BeamLight` processes a 3 h window per day of a
17,149-file Input Dataset. Its postscript gave the exact names
`data_SBND2026A_Auto_POMS_SBND2026A_Gen2_Run3_BeamLight_v10_14_02_08_{reco1,larcvreco1,histreco2,caf,flatcaf}_sbnd`
and SAM held 121 files under each `Dataset.Tag`. The lineage scan read
inputs in list order and stopped after 20 inputs with children (46 probed);
those only had children from an earlier campaign
(`..._BeamLight_Fixed_Dev_Data_..._sbnd`, correctly rejected), so the result
was "no output definition found". Reading names from lineage assumed nearly
every input was already processed.

A candidate now counts only when it is a genuine output of this stage:

- SAM has files under it (`countFiles(dimensions="dataset.tag <name>")` > 0);
- up to 5 of its files (`listFiles(dimensions="dataset.tag <name> with limit 5")`)
  reach a file of the original Input Dataset by walking first parents, at most
  6 steps; the depth is the number of steps (the smallest of the sample).
  Membership is one query per level:
  `listFiles(dimensions="defname: <input> and file_name a,b,c")`. A retired or
  unknown file ends its walk and counts as not reached;
- the files' `production.name` and `sbnd_project.version` do not contradict
  `MT_PRODUCTIONNAME` and the version of the launch command (a field absent
  on either side is skipped).

A failing candidate is listed in step 7 with its reason (no files in SAM,
files not descendants of this Input Dataset, metadata mismatch). Test Launch
names are never candidates. Lineage stays as the cross-check: a tag that
carries the stage's identity but matches no pattern still raises. The
depth recorded is the verified one.

## Lookup by Submission id

POMS's `show_campaign_stages` hides inactive campaigns, so a finished
campaign may have no stage to look up. `show_output_definitions.py
--submission <id>` takes the launch command from
`submission_details(<id>)["submission"]["command_executed"]` and the Input
Dataset from `["submission"]["campaign_stage_snapshot_obj"]["dataset"]`;
campaign and stage names are not needed. The call is
`PomsSession.get_submission_source(id)`. Still read-only.

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
6. **candidates** (patterns only): the names to verify and where each came
   from (named by the postscript, SAM definition, lineage).
7. **verification** (patterns only): candidates kept with their depth, and
   dropped with the reason.
8. **classification**: with patterns, lineage tags not verified are dropped
   as a disagreement (carries this stage's production name and version, no
   pattern), as failed verification, or as another campaign's. Without
   patterns, the fallback scope string, the tags that carry it kept and the
   rest dropped. Steps 6 and 7 are absent then; numbers are fixed, not
   renumbered.
9. **result**: the definitions recorded, or why nothing was recorded.

A launch command that is missing ends the trace after step 1 and step 9.
