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
  `test/debug_raw_call.py`, which replicates its auth/POST logic without the
  buggy formatting.
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

`PomsSession.submit_next_slice()` therefore calls
`pc.make_poms_call(method="launch_jobs", ...)` directly (the same endpoint
both wrappers post to) and parses `submission_id` out of the redirect URL's
query string itself — the correct way, already proven against real
production launches (see `logs/` history). It also passes `test_launch=1`
straight to that same `make_poms_call` when `config.ini`'s
`[decision] test_launch` is enabled, rather than routing through either
broken wrapper.

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
