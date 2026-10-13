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
  four campaigns (ADR-0022), so "one file per input" over everything under
  the input would retire other campaigns' data.

## Decision

`cleanup.run_cleanup(cfg, session)` runs the whole cleanup in-process, under
the run's lock: get the definitions, plan, retire, verify. The work is in
`scripts/duplicate_cleanup.py`, which takes the SAM client as an argument.

- **Scoped to the stage's own definitions.** Cleanup only looks at the Output
  Definitions that `output_definitions.py` infers for this Campaign Stage
  (ADR-0022). A recorded flat list of names is not trusted: it is inferred
  again and replaced before planning. If the definitions cannot be inferred,
  nothing is cleaned and the switch stays on.
- **Kept Worker, by process id.** In a standard campaign every Output Tier of
  an input is a direct child of the initial fcl file. The tiers are siblings,
  so lineage cannot tie one tier's file to another's. Only job identity can:
  files written by one job share a SAM process id. Per input, the Kept Worker
  is the process id with files in the most definitions; the lowest id wins a
  tie. Every file of that input from another worker is retired, in every
  tier. An input with one worker is left alone.
- **Fallback.** If any file of an input has no process id (seen on aurora
  outputs), that input keeps the first file by name in each definition. The
  plan marks the input as `fallback`.
- **Deepest first.** Definitions are cleaned one after another, from the
  deepest Output Tier to the shallowest, each finishing before the next
  starts. A file is settled after its children.
- **Children guard.** A file is retired only if it has no children, or all
  its children are retired by the same plan. A file with other children (for
  example a later campaign used it as input) is left in place. It is listed
  in the plan and the log, and does not fail verification.
- **Retire only.** A duplicate is retired in SAM. No location is removed and
  nothing is deleted from disk. For each cleaned definition a definition
  `<definition>_retired_files` is created
  (`dataset.tag <definition> and availability: retired`), so the retired
  files stay easy to find.
- **Plan, apply, verify.** The plan is written to
  `logs/<campaign_name>/cleanup_plan_<ini name>.json` before anything
  changes. Progress is kept in the State File's `cleanup` section
  (`plan_path`, `done`, `finished`), so a failed run resumes the same plan.
  Verification checks that each input has only its kept files live in each
  definition (plus the files the children guard left), and that every kept
  file's parents are live.
- **The switch goes off only after verification passes.** Any failure leaves
  it on, is logged, and is retried on the next run.
- **`--dry-run` writes the plan and retires nothing.**

## Putting files back, or deleting them

The script does neither. Both are left to the operator.

- **Put files back.** Build a new definition from the cleaned one plus its
  retired-files one, for example
  `samweb create-definition <new name> "defname: <definition> or defname: <definition>_retired_files"`.
  The files are still on disk with their locations, so the new definition is
  usable as it is. This recipe has not been tried on live SAM yet; check the
  file count of the new definition before using it.
- **Delete from disk.** Take the file list from `<definition>_retired_files`
  and delete by hand, once the cleaned definitions have been checked.

## Consequences

- Storage is not freed by cleanup. Someone has to delete the retired files.
- A first run on a large campaign is slow: one tier can hold about 250k
  files, read with `getMultipleMetadata` in batches, and files are retired
  one at a time. The lock is held throughout, so the hourly runs in between
  log "previous run still active" and skip.
- The plan is made once. A file that appears after planning is not in it and
  fails verification, which names its input. To plan again, delete the
  `cleanup` section from the State File.
- The shared process id was confirmed on one standard campaign. Check it on
  a new campaign type with a dry run before relying on it; the plan's
  `fallback_used` says whether any input fell back.
- `scripts/duplicate_cleanup.sh` is removed. The original
  `sam_delete_duplicates` script in the production software area is not
  touched and not used.
- A dry run with the gate open reads SAM and records the inferred Output
  Definitions in the State File, the same as a real run would.
