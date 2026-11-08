# Set the stage's Input Dataset per slice from a template

## The problem

Submitting a slice used to mean launching against whatever Input Dataset the
stage already had, leaving POMS to divide it with the stage's split type. For
a stage fed pre-built per-slice datasets, the split types must be cleared:
POMS's launch otherwise failed creating its own definition through the SAM
`api/definitions/create` call (HTTP 400, observed on the AR23 plus campaign).
Slices are then SAM definitions built by hand, one per submission, named
`<input>_slice<n>_files<k>`. Nothing set the stage to them.

## Decisions

- **`[decision] input_dataset_template` is required, with no fallback.**
  `load_config` raises a `ValueError` naming the key if it is missing, or if
  the value has no `{n}` placeholder. A silent fallback would submit every
  slice against the same dataset.
- **`submit_plan()` sets the dataset before each submission.** It calls the new
  `PomsSession.set_input_dataset()` with the template filled with
  `cfg["last_split"]`, which counts submitted slices and so is the next
  0-indexed slice number. `last_split` is persisted after each success, as
  before. `--dry-run` logs the dataset names it would use and makes no POMS
  call.
- **`set_input_dataset()` leaves `cs_last_split` alone**, unlike
  `set_recovery_input_dataset()`. With the split types cleared, POMS's own
  split counter has no meaning for these stages.
- **Recovery keeps its dataset.** `recovery.py` switches the stage to the
  Recovery Dataset and then calls `submit_plan(..., set_input_dataset=False)`.
  Without that, the slice template would overwrite the Recovery Dataset. The
  template names slice datasets only, so recovery for such a stage needs the
  Recovery Dataset to be built from the original input as usual (`do_recovery`).
- **Test launches do not use the template.** A Test Launch is outside the
  planning machinery and never reaches `submit_plan()`. The operator sets the
  stage's dataset by hand before one.

## Consequence

Every ini needs the key, including those for stages that still split
automatically. For those, set a template naming datasets that exist, or keep
using a branch from before this change. Slice datasets must exist for all
`last_split` values below `max_splits`.
