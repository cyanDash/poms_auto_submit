# poms_client gotchas

Facts about the vendored/CVMFS `poms_client` library (and the POMS server
behind it) that `scripts/poms_session.py` exists to hide from the rest of the
codebase. Read this before touching `poms_session.py` — several lines there
look like they could be simplified but exist to route around one of these.

## Real response shapes

`poms_client.py`'s docstrings imply flatter shapes than what the server
actually returns. Confirmed live against production POMS:

- `campaign_stage_submissions()` → the submissions list is nested at
  `resp["data"]["submissions"]`, not at the top level. Each entry has a
  plain `status` string (`"Located"`, `"Running"`, `"New"`, `"Failed"`, ...)
  but no `pct_complete`.
- `submission_details()` → `pct_complete` is nested at
  `details["submission"]["pct_complete"]`. There's no flat `status` field on
  the submission object (only derivable from a numeric `history`/`statusmap`,
  not worth parsing — get status from `campaign_stage_submissions()` instead).
  The grid job id (`"71717566@jobsub03.fnal.gov"`-style) is also here, at
  `details["submission"]["jobsub_job_id"]` — `get_progress()` logs it
  alongside `pct_complete`. `details["submission"]["submission_params"]["test"]`
  is `1` when the submission was a Test Launch (see CONTEXT.md's "Test
  Launch" entry) — confirmed against real Test Launch submissions.
- `submission_details()` → there's no flat `subgroup` field, and it isn't in
  `submission_params` either. It only shows up inside
  `details["submission"]["command_executed"]`, the literal jobsub command
  POMS ran, as a `--subgroup=<value>` flag — see "Where `subgroup` actually
  lives" below for why this is the field to read, not `param_overrides`.
- `show_campaign_stages()` → the stage list is nested at
  `resp["campaign_stages"]`, not the top-level list its docstring implies.

## Known upstream bugs (unpatched, in vendored/external code)

- **Server**: `StagesPOMS.get_campaign_stage_name()` returns a raw
  SQLAlchemy `Row` from `.first()`, which isn't JSON-serializable — any call
  through it with `fmt=json` (what `poms_client.py`'s wrapper always
  requests) 400s with "Object of type Row is not JSON serializable". Avoid
  calling it; resolve stage names some other way (e.g. the reverse
  `get_campaign_stage_id()`, which works fine).
- **`get_campaign_name(experiment, campaign_id)`** was observed silently
  returning `''` instead of the real name when called without `role=`, under
  the `sbndpro` managed-token identity — passing `role="production"`
  explicitly fixed it there. Oddly, the same no-`role=` call worked fine
  under a different (personal, non-`sbndpro`) identity, so the exact
  mechanism looks identity/session-scope dependent, not purely about the
  kwarg. Either way: always pass `role=` explicitly, it doesn't raise when
  it's silently wrong. A downstream symptom if you don't: passing that empty
  `campaign_name` into `get_campaign_stage_id()` doesn't raise either — the
  server returns the literal string `"null"`, which crashes `int("null")`
  inside `poms_client.py`.
- **Client**: `make_poms_call()` has `if res.find("Traceback"):` — `str.find`
  returns `-1` (truthy) when the substring isn't found, so this branch is
  taken on *every* error response, not just tracebacks, and mangles the real
  error body down to almost nothing before raising `RuntimeError`. To see
  what the server actually said, bypass `make_poms_call()` — see
  `raw_poms_call()` in `scripts/poms_raw_client.py`, which replicates its
  auth/POST logic without the buggy formatting. Both `PomsSession.submit_next_slice()`
  (production) and `test/debug_raw_call.py` (manual debugging) call it — see
  "Distinguishing a genuine campaign end from a real failure" below.
- **Server**: `pct_complete` can stop being recalculated for a submission
  while it's still nominally active, leaving it stuck at a stale (often
  near-zero) value indefinitely. Observed live 2026-08-30 on submission
  3136636 (campaign_stage_id=26971, `decode_reco1_reco2_caf`), captured with
  `test/get_submission_progress.py`:
  ```json
  {
    "submission_id": "3136636",
    "submission": {
      "files_consumed": 9982,
      "files_generated": 10000,
      "pct_complete": 0.043215211754537596
    },
    "history": [
      {"created": "2026-08-28T09:03:36", "status_id": 4000},
      {"created": "2026-08-28T11:37:25", "status_id": 4000}
    ],
    "statuses": [
      ["Available output: ", 38804, "..."],
      ["Submitted to SAM: ", 10000, "..."],
      ["Consumed by SAM: ", 9982, "..."],
      ["Pending: ", 299, "..."]
    ]
  }
  ```
  `pct_complete` reads as essentially 0%, but `files_consumed` and the
  `statuses` array (computed fresh per call from live SAM dimension
  queries, not cached like `pct_complete`) both show ~97-99% real progress.
  `history` confirms it's `pct_complete` itself that's stuck: repeated
  `status_id=4000` ("Running") entries stop dead at `11:37:25` — no new
  entry in the two days since. But the `statuses` array can *also* be
  stuck/wrong (observed the same day on a different submission, 3138475 —
  all four counts read `0` because the dims strings reference
  `snapshot_for_project_name None`, the submission having never gotten
  attached to a real SAM project while stuck in `New`). Neither POMS-side
  signal is trustworthy alone — see
  `docs/adr/0007-condor-q-primary-progress-source.md` for why `condor_q`
  (real HTCondor state) is now the primary progress source, with these two
  as a fallback chain. `history[].created`/`submission.created` are naive
  strings in Central time (the POMS host and every `poms_auto_submit` cron
  host are both FNAL/CDT, so a plain `datetime.fromisoformat`/
  `datetime.now()` comparison is valid without extra timezone handling).
  `PomsSession.get_progress()` surfaces `last_status_change` (max
  `history[].created`) and `files_submitted`/`files_pending` (from the
  `statuses` labels `"Submitted to SAM: "`/`"Pending: "`) for
  `poms_auto_submit.py`'s fallback chain to use.

## `param_overrides` must be pre-serialized

`update_stage_param_overrides()` POSTs via `requests` with `data=kwargs`.
`requests`' form-encoder treats a dict *value* as iterable and flattens it
down to just its keys, silently dropping the values — so passing
`param_overrides={"-Osubmit.subgroup=": "pro"}` (a raw dict) sends a
malformed request and the server 400s. The server does
`OrderedDict(ast.literal_eval(param_overrides))`, so the client must
pre-serialize the value itself: `param_overrides=str(list(updates.items()))`.
This is why `PomsSession.update_stage_params()` builds that string instead of
passing `updates` directly.

A real success response looks like
`str((stage.param_overrides, stage.test_param_overrides))`, e.g.
`"([['--stage=', '...'], ...], None)"` — a single string, not an `(ok, data)`
tuple. Treat `None` as failure, any other string as success.
`test_param_overrides` is the override set a Test Launch uses instead of
`param_overrides` — see CONTEXT.md's "Test Launch" entry.

Deletion semantics (submitting a falsy value for a key removes it —
`opo.pop(k, None)` server-side) are read from server source only, not
live-verified.

## `launch_campaign_stage_jobs()`/`launch_campaign_jobs()` crash on success

`launch_jobs` returns an HTTP 303 redirect with the new `submission_id` in
the redirect URL's query string, not an `(ok, data)` tuple. `poms_client.py`
does have higher-level wrappers for it —
`launch_campaign_stage_jobs(campaign_stage_id, test_launch=None, ...)` and
`launch_campaign_jobs(campaign_id, test_launch=None, ...)`, both of which
also support `test_launch` (see CONTEXT.md's "Test Launch" entry) — but
**don't use them**: both assume the redirect body ends in `"_<digits>"` and
do `int(data[data.rfind("_") + 1:])` to pull out the submission id. The real
redirect is a URL like
`".../list_launch_file/sbnd/analysis?campaign_stage_id=26938&submission_id=NNN"`,
so `rfind("_")` lands inside `"submission_id"` itself and `int()` raises
`ValueError` — with no `try`/`except` around it, on every *successful* (303)
call. Confirmed live 2026-08-14, see `test/manual_test_launch.py`'s
docstring for the full story.

`PomsSession.submit_next_slice()` therefore posts to the same `launch_jobs`
endpoint both wrappers use and parses `submission_id` out of the redirect
URL's query string itself — the correct way, already proven against real
production launches (see `logs/` history). It also passes `test_launch=1`
when `config.ini`'s `[decision] test_launch` is enabled, rather than routing
through either broken wrapper.

### Distinguishing a genuine campaign end from a real failure

A non-303 `launch_jobs` response isn't necessarily worth alerting on: once a
campaign stage's Input Dataset runs out of unclaimed batches, POMS refuses
further `launch_jobs` calls with a body containing the literal text
`AssertionError('No more splits in this campaign.')` (confirmed live
2026-09-02 against `campaign_stage_id=26971`, `decode_reco1_reco2_caf`, after
its 6th successful slice). That's expected, normal completion, not a bug.

Because of `make_poms_call()`'s mangling bug above, this text is unreachable
through the normal call path — by the time `make_poms_call()` raises, the
body has already been sliced down to nothing. `submit_next_slice()` therefore
no longer calls `pc.make_poms_call()` at all: it calls `raw_poms_call(pc,
"launch_jobs", ...)` (`scripts/poms_raw_client.py`, mirroring
`make_poms_call()`'s auth+POST plumbing verbatim, minus the buggy formatting)
so it can check the real body for `NO_MORE_SPLITS_MARKER`. A match returns
`None` (treated by `poms_auto_submit.py`'s `run()` as graceful campaign
completion — it stops submitting further planned slices that run, without
touching `last_split`); anything else raises `RuntimeError` with the real,
unmangled body — a useful side effect: any *other* `launch_jobs` failure is
now fully diagnosable straight from the cron log, no more need to manually
re-run `debug_raw_call.py`. `debug_raw_call.py` itself calls the same
`raw_poms_call()` rather than keeping its own copy of this plumbing — see
`docs/adr/0009-bypass-make-poms-call-for-launch-jobs.md` for the decision and
its update.

## Example `submission_details()` response (trimmed)

Captured live 2026-08-25 against `test_poms_auto_submit_PDS_Detvar3_sdas1`
(campaign_id=11503, campaign_stage_id=27002), submission_id=3135073, a real
Test Launch. Trimmed down to the fields that matter above — the real
response is ~500 lines, mostly repeated `campaign_stage_obj`/experimenter
snapshots:

```json
{
  "submission_id": "3135073",
  "submission": {
    "submission_id": 3135073,
    "campaign_stage_id": 27002,
    "created": "2026-08-25T17:20:59",
    "submission_params": {
      "dataset": "aurora_SBND2026A_gen2_BNBLight_DevSample_..._slice0_files5",
      "test": 1
    },
    "jobsub_job_id": "71717566@jobsub03.fnal.gov",
    "pct_complete": 100.0
  },
  "statusmap": {
    "1000": "New",
    "2000": "LaunchFailed",
    "3000": "Idle",
    "4000": "Running",
    "5000": "Held",
    "6000": "Failed",
    "7000": "Completed",
    "8000": "Located",
    "9000": "Removed",
    "2400": "Awaiting Approval",
    "2500": "Approved",
    "1500": "Cancelled"
  }
}
```

That `statusmap` is the authoritative list of Submission `Status` values —
CONTEXT.md's "Status" entry is sourced from it (note: `Completed`, not
`Finished`, for status id `7000`).

The full, untrimmed response this excerpt came from (a different submission,
3135404, captured 2026-08-26) is saved verbatim at
`docs/raw/submission_details_3135404.json` — treat that file as the source
of truth; this excerpt is commentary on top of it, not a replacement (see
[[feedback_save_full_api_responses]] in memory for why the earlier trimmed
excerpt above cost us the finding below).

## Where `subgroup` actually lives

`subgroup` shows up in three places in the full response, and they can
disagree:

1. `submission.campaign_stage_obj.param_overrides` — the stage's **current**
   `param_overrides`. Unreliable for the same reason ADR-0002 already found:
   a later run's `set_subgroup()` call overwrites it, so by the time you read
   it back it may no longer reflect what this particular submission launched
   with (in the captured example, it has no `subgroup` key at all anymore).
2. `submission.campaign_stage_snapshot_obj.param_overrides` — a snapshot of
   the *general* `param_overrides` frozen at the moment this submission
   launched. Immune to (1)'s staleness, but only reflects what would have
   been used for a *regular* (non-Test) launch.
3. `submission.command_executed` — the literal jobsub command POMS actually
   ran, containing a `--subgroup=<value>` flag. This is the one immutable,
   per-submission ground truth, valid for both regular and Test Launches.

(2) and (3) can disagree: in the captured example, `command_executed` has
`--subgroup=test` while the snapshot's `param_overrides` has
`-Osubmit.subgroup=pro`, because the submission was a Test Launch
(`submission_params.test == 1`). Test Launches use `test_param_overrides`
server-side instead of `param_overrides` (see CONTEXT.md's "Test Launch"
entry) — so whatever `PomsSession.set_subgroup()` set on the general
override that run had **no effect** on the actual submitted job; POMS used
the stage's `test_param_overrides` (in the example, hardcoded to
`subgroup=test`) instead. Concretely: while `[decision] test_launch = 1`,
every submission gets whatever subgroup `test_param_overrides` says — never
`pro`, regardless of what the script requested.

`PomsSession.get_progress()` therefore parses `subgroup` out of
`command_executed` (`SUBGROUP_COMMAND_PATTERN` in `poms_session.py`), not
out of either `param_overrides` variant.

## samweb CLI output shapes (scripts/run_recovery.sh)

Confirmed live 2026-09-02 against
`aurora_SBND2026A_gen2_BNBLight_DevSample_prodgenie_corsika_proton_rockbox0p1_sbnd_CV_v10_14_02_03_reco1_sbnd`
(untrimmed captures under `docs/raw/samweb_*.txt`):

- `list-definition-files <defname>` — one filename per line, no header.
- `file-lineage children <file>` — one filename per line; `children` (not
  `descendants`) matches this project's single-Campaign-Stage convention
  (CONTEXT.md) where a stage's executables run as one job, so outputs are
  direct children of the input file.
- `get-metadata <file>` — right-padded `Key: value` lines, one per field.
  `Dataset.Tag`'s line matches TODO.md's `grep 'Dataset.Tag' | awk -F': '`
  approach directly. Multi-line values (e.g. `Checksum`'s `adler32`/`md5`
  continuation lines) have no `": "` and are silently skipped by that same
  parsing approach.
- `count-definition-files <defname>` — a single bare integer on stdout.
  Simpler than parsing `list-definition-files --summary`'s `Key:\tvalue`
  block (tab-separated, confirmed live but not used by the shipped script).

## `defname: X with limit N` doesn't create a stable, boundable subset

Building a "small" test dataset as `defname: <big_dataset> with limit 10`
(a natural way to grab a quick 10-file subset) does **not** behave like a
frozen 10-file snapshot once something else wraps another `with limit/offset`
around it. Confirmed live 2026-09-02: a manual `poms_auto_submit` test
against exactly this kind of dataset (`cs_split_type=nfiles(10)`, base
dataset built via `with limit 10`) produced two POMS-generated per-submission
slices, `..._slice0_files10` (`defname: <small> with limit 10 offset 0`) and
`..._slice1_files10` (`defname: <small> with limit 10 offset 10`) — despite
`samweb count-definition-files <small>` reporting exactly `10` files total,
`slice1` (offset past the supposed end) still resolved to 10 real, different
files rather than an empty set. SAM's dimension resolver apparently inlines
the nested `defname: <small>` back out to its own dimension text
(`defname: <big_dataset> with limit 10`) rather than treating it as an
already-materialized 10-file result, so the outer `with limit 10 offset 10`
ends up querying the **original big dataset**, not the intended small
subset — silently escaping the boundary a human would expect `with limit`
to enforce.

Taking a `samweb take-snapshot` of either side (the big dataset, or the
small `with limit 10` one itself) does **not** fix this — confirmed live the
same day: `samweb list-files "defname: <snapshotted small> with limit 10
offset 10"` still returned 10 real files. That rules out dataset
staleness/dynamism as the cause. The real mechanism: nesting *any*
`defname: X` inside another dimension expression, snapshotted or not,
recursively expands back to `X`'s own dimension text rather than treating
`X` as an already-materialized result — so an outer `with limit/offset`
composes against whatever's at the bottom of that expansion chain, not
against a bounded N-item window. Nested `with limit`/`offset` clauses
effectively don't compose at all; only the outermost one constrains
anything.

Practical consequence: don't use `with limit N` (snapshotted or not) to
build a dataset meant to exercise this project's exhaustion/recovery path —
POMS's own slicing re-wraps the campaign stage's dataset in further `with
limit/offset` clauses, exactly the scenario that breaks. Use a dimension
with no nested `defname:` to expand — an explicit file list is a leaf
predicate, confirmed live to behave correctly (`with limit 10 offset 10`
against it correctly came back empty):
```bash
samweb list-definition-files <big_dataset> | head -10 > files.txt
samweb create-definition <small_dataset> "file_name $(paste -sd, files.txt)"
```
This does **not** affect `scripts/run_recovery.sh`'s own recovery dataset:
its dimension (`not isparentof: (...)`) is a real membership predicate over
the *original* input dataset, not a nested `defname:` wrapped in `with
limit/offset`, so it isn't subject to this composition trap.

## update_campaign_stage sets dataset and cs_last_split -- confirmed live

`PomsSession.set_recovery_input_dataset()` calls `raw_poms_call(pc,
"update_campaign_stage", campaign_stage=..., dataset=..., cs_last_split=0)`
to point a campaign stage at a new Input Dataset and reset its split
counter (TODO.md step 7). `poms_client.py`'s own `update_campaign_stage()`
wrapper is unusable for this: it routes through `make_poms_call()` (this
doc's `if res.find("Traceback"):` bug applies), and even on success returns
the literal string `"status_code"` instead of the real response.

Confirmed live 2026-09-02 via `test/manual_test_update_campaign_stage.py`
against `campaign_stage_id=27002` (`scrub_detsim_reco1_reco2_caf`,
`test_poms_auto_submit_PDS_Detvar3_sdas1`) — despite the POMS GUI's
"sam_settings" tab not exposing "Last Split" for editing, the raw API call
does honor it: `status=200`, `data="Success"`, and `cs_last_split` read back
as `8` before the call, `0` after (full before/after stage dicts saved at
`docs/raw/update_campaign_stage_27002.json`). `dataset` independently
confirmed too, in a second call: set to `"something_silly"` (read back as
exactly that in `after`), then restored to the real dataset name in a third
call (`docs/raw/update_campaign_stage_27002_dataset_change.json`) — genuinely
takes effect, not just accepted-and-ignored.

Operational note: this call is destructive to the target stage's submission
progress bookkeeping -- resetting `cs_last_split` mid-campaign means the
next `launch_jobs` starts allocating splits from batch 0 again. Fine for
`recovery.py`'s use (a genuinely new Input Dataset), not something to run
against a stage with real in-progress work you want preserved.
