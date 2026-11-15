# Cleanup keeps one worker's outputs per input, deepest tier first, and only retires

## Context

Cleanup ran `sam_delete_duplicates` once per output definition, in the
background, through `scripts/duplicate_cleanup.sh` (ADR-0016). Faults:

- **Each tier kept an arbitrary file.** For one input, the surviving reco1
  file and the surviving larcv file could come from different jobs, so from
  different simulations. Seen on a standard campaign: one input had larcv
  files from two workers and a reco1 file from only one of them.
- **Nothing was waited for.** The switch went off and "done" was logged
  before any file was retired. A failure was invisible.
- **No plan and no check.** An operator could not see what would be retired,
  nor confirm the result afterwards.
- **Inputs are shared between campaigns.** One input can have children from
  four campaigns (ADR-0023), so "one file per input" over everything under
  the input would retire other campaigns' data.

## Decision

`cleanup.run_cleanup(cfg, session)` runs the whole cleanup in-process, under
the run's lock: get the definitions, plan, retire, verify. The work is in
`scripts/duplicate_cleanup.py`, which takes the SAM client as an argument.

- **Scoped to the stage's own definitions.** Cleanup only looks at the Output
  Definitions that `output_definitions.py` infers for this Campaign Stage
  (ADR-0023). If the definitions cannot be inferred, nothing is cleaned and
  the switch stays on.
- **Kept Worker, by process id.** In a standard campaign every Output Tier of
  an input is a direct child of the initial fcl file. The tiers are siblings,
  so lineage cannot tie one tier's file to another's. Only job identity can:
  files written by one job share a SAM process id. Per input, the Kept Worker
  is the process id with files in the most definitions; the lowest id wins a
  tie. A file of another worker is retired only when a live file stays in the
  same (input, Output Definition); see "Only true duplicates are retired".
  An input with one worker is left alone.
- **Chained stages keep one chain.** When a deeper tier is written by other
  jobs (a caf job reading a reco1 file), one process id per input is wrong:
  the caf of the kept reco1 has another id. So the Kept Worker is chosen
  among the files nearest the input, and a worker counts the definitions
  covered by its files plus their descendants. Below that, a file whose
  parent is retired (by this plan or before it) is retired, and the files
  under one kept parent are
  grouped by that parent and settled by the same rule. A flat campaign has
  one group per input, so nothing changes for it.
- **A parent retired before the plan is recognised by its absence.** SAM
  answers for a retired file as for one it never had (see "What SAM says
  about a retired file" below). So an ancestor that a live file's metadata
  names, that the bulk metadata read leaves out, and that `getMetadata`
  reports as `FileNotFound`, is gone. Its Dataset.Tag cannot be read, so it
  counts as one of the stage's retired files only when it sits at the depth
  of a cleaned definition; the files under it are then retired. At any
  other depth the file under it is `ungrouped` and the cleanup stops. The
  parents of a gone file cannot be read either, so the input of the files
  under it is unknown: the plan lists them under the retired ancestor's
  name instead of an input's.
- **Fallback.** If any file of a group has no process id (seen on aurora
  outputs), that group keeps the first file by name in each definition. The
  plan marks the input as `fallback`.
- **A file that cannot be grouped stops the cleanup.** A file with no
  metadata or no parent, or under an ancestor SAM no longer has at a depth
  where the stage has no definition, or whose recorded depth does not lead
  to an input
  (it stops at one of the stage's own output files, or disagrees with an
  ancestor's depth), is listed as `ungrouped` in the plan. The cleanup then
  fails before anything is retired, names the files, and records no
  `cleanup` section, so the next run plans again.
- **Deepest first.** Definitions are cleaned one after another, from the
  deepest Output Tier to the shallowest, each finishing before the next
  starts. A file is settled after its children.
- **Children guard.** A file is retired only if it has no live children, or
  all its live children are retired by the same plan. A child that is
  already retired does not count, so a plan made after a partial apply still
  retires the parent. A file that SAM no longer has when its children are
  read has none. A file with other live children (for example a later
  campaign used it as input) is left in place. It is listed in the plan and
  the log, and does not fail verification.
- **Retire only.** A duplicate is retired in SAM. No location is removed and
  nothing is deleted from disk. No SAM definition of the retired files is
  made: SAM cannot list a retired file, so it would be empty.
- **Full paths are listed first, and that list is the record.** Before the
  first file of a definition is retired, the full paths of its files to
  retire are written to
  `logs/<campaign_name>/<definition>_retired_files.txt`, one per line. If
  the list cannot be written, nothing in that definition is retired. A
  resume keeps the lines already written; a file already retired that is
  missing from the list is added by name only, since SAM no longer gives
  its location.
- **Plan, apply, verify.** The plan is written to
  `logs/<campaign_name>/cleanup_plan_<ini name>.json` before anything
  changes. Progress is kept in the State File's `cleanup` section
  (`plan_path`, `done`, `finished`), so a failed run resumes the same plan.
  Verification checks that each input has only its kept files live in each
  definition (plus the files the children guard left), and that every kept
  file's parents are live.
- **The switch goes off only after verification passes.** Any failure leaves
  it on, is logged, and is retried on the next run.
- **Per-file lines go to `logs/<campaign_name>/<campaign_stage_name>_cleanup.log`**,
  through a logger that does not propagate; the campaign log keeps the
  summary lines, so a 250k-file tier does not flood it.
- **`--dry-run` writes the plan and the lists of full paths, and retires
  nothing.** The inferred Output Definitions are not recorded and no
  `cleanup` section is made.

## Only true duplicates are retired (2026-11-15)

Operator decision, reversing "all outputs of an input from other workers
retired in every tier" (#16 user story 13). A definition the kept worker has
no file in keeps exactly one file, the first by name, from the next-best
worker that has one there (same ranking: most definitions covered, then
lowest process id). In a chained stage this applies in each group, under the
kept parent. The input is marked `"mixed_workers": true` in the plan with
`"rescued_from": {definition: [process ids]}`, and the cleanup log says so.
No (input, definition) that had a live file is emptied. Verification follows
the plan: the kept files, rescued ones included, are the ones expected live.

Why: the real dry run of 2026-10-13 on the torch-vs-triton stage would have
retired 398 single-copy histogram files, made by later jobs (one worker made
detsim_torch, detsim_triton and torch_triton_comparison_hist; another made
torch_torch_repeat_comparison_hist). Those were not duplicates of anything.

Not covered: in a chained stage, a definition that exists only under a
retired file's descendants is still retired with it.

## What SAM says about a retired file

Probed on live SAM on 2026-10-13
(`docs/raw/sam_retired_file_2026-10-13.txt`, summarised in
`docs/poms_client_gotchas.md`). A retired file is still listed among its
parent's children. Every query on the file itself fails or leaves it out:
`getMetadata`, `getFileLineage` and `locateFile` raise `FileNotFound`,
`getMultipleMetadata` omits it, and `listFiles` never returns it, not even
with `availability: retired`.

An earlier version of this decision created a definition
`<definition>_retired_files` (`dataset.tag <definition> and availability:
retired`) for each cleaned definition. That definition would be empty, so
it was dropped.

## Putting files back, or deleting them

The script does neither. Both are left to the operator.

- **A retirement cannot be undone through a SAM query.** No definition can
  select the retired files. `<definition>_retired_files.txt` is the only
  record of them, so it must be kept.
- **Put files back.** Declare the files again from the paths in
  `<definition>_retired_files.txt` with fife_utils' `sam_add_dataset`. The
  files are still on disk. This is untested: it has not been tried on live
  SAM, and it is not known whether the new declarations get the old
  metadata and parents. Try it on a few files first.
- **Delete from disk.** Take the paths from `<definition>_retired_files.txt`
  and delete by hand, once the cleaned definitions have been checked.

## Consequences

- Storage is not freed by cleanup. Someone has to delete the retired files.
- A first run on a large campaign is slow: one tier can hold about 250k
  files, read with `getMultipleMetadata` in batches, and files are retired
  one at a time. The lock is held throughout, so the hourly runs in between
  log "previous run still active" and skip.
- The plan is made once. A file that appears after planning is not in it and
  fails verification, which names its input. To plan again, delete the
  `cleanup` section from the State File. This is safe after a partial apply:
  the children guard ignores the children already retired.
- A recorded depth that is too shallow is caught when the walk stops at one
  of the stage's live files. If it stops at a retired one, that file cannot
  be told from a retired input: the file under it is grouped under it and
  kept, and verification then fails because its parent is not live. A wrong
  depth shows on the live files of the same definition first, and those stop
  the cleanup before anything is retired.
- A recorded depth that is too deep is only caught when the walk runs out of
  parents. If the input itself has a parent, the file is grouped under that
  parent instead.
- The shared process id was confirmed on one standard campaign. Check it on
  a new campaign type with a dry run before relying on it; the plan's
  `fallback_used` says whether any input fell back.
- `scripts/duplicate_cleanup.sh` is removed. The original
  `sam_delete_duplicates` script in the production software area is not
  touched and not used.
- A dry run with the gate open reads SAM (lineage, metadata, locations) and
  changes nothing there.
- No production code creates a SAM definition for cleanup.
