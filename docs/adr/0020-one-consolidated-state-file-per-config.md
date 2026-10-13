# One consolidated state file per config; the script never writes the ini

Internal state had grown to one small file per feature in the campaign's
`logs/<campaign_name>/` directory (`submission_cache_<id>.json` from ADR-0008,
`stuck_no_data_<id>.json`, `stragglers_<id>.json` from ADR-0018,
`output_definitions_<id>.txt`), and the script also rewrote three keys in the
operator's ini: `last_split`, `recovery_handled` and `switch`. All of it now
lives in one file per ini, `logs/<campaign_name>/state_<ini name>.json`, behind
`scripts/state.py`.

## Decisions

- **One top-level section per owner**: `submissions`, `stuck_no_data`,
  `stragglers`, `recovery_handled`, `output_definitions`, `last_split`,
  `switch`. Each owner reads and replaces only its own section through
  `state.read()`/`state.write()` and keeps its own tolerance for malformed
  entries.
- **Per ini, not per Campaign Stage.** The path comes from the ini's file
  name, so it is known in `load_config()` without a POMS call and `switch`
  can still be checked before POMS is contacted. Two inis pointing at one
  stage have fully separate state.
- **The ini is read-only for the script.** `last_split` and `switch` are
  removed from the template. An ini written before this change that still
  carries them supplies the starting value; once the state file holds a
  value, the ini's is ignored.
- **`switch` is no longer an operator control.** Cleanup turns it off when
  the campaign is done. An operator stops a campaign by stopping its cron
  job, and re-runs a finished ini by setting `"switch": true` in its state
  file.
- **A write re-reads the file and replaces one section**, so an owner holding
  an in-memory copy of its section (PomsSession) cannot overwrite another's.
  The file is written to a temp name and renamed, because a torn write would
  lose every section and not just one.
- **`run_recovery.sh` is unchanged.** It still writes its output defnames to
  a scratch `output_definitions_<id>.txt` (its prestage log is named after
  that path). `recovery.py` moves the list into the state file and deletes
  the scratch file. A Test Launch's list is dropped rather than diverted to
  a sibling file as in ADR-0016.
- **The old per-stage files are not imported.** They are named by stage id,
  which the per-ini path does not carry, and what they hold is safe to lose:
  the submission cache refills from POMS and the stuck and Straggler counts
  restart. They are left on disk, unused.

## Consequences

- A cleanup run only sees output definitions recorded under its own ini. A
  separate cleanup-only ini for a stage another ini recovered finds none
  until issue #16's lineage-based discovery replaces the recorded list.
- A malformed state file reads as empty for every section at once:
  `last_split` falls back to the ini value or 0, `switch` to on, and
  `recovery_handled`/`output_definitions` are lost until restored by hand.
- Resetting or correcting `last_split` means editing the state file;
  `max_splits` is still set in the ini.
- ADR-0018's "state lives in its own per-Campaign-Stage file" no longer holds.
