# Bypass make_poms_call() for launch_jobs to detect campaign exhaustion

## The problem

`decode_reco1_reco2_caf`'s hourly cron run failed every hour for 24+ hours
after its 6th successful slice: `submit_next_slice()` called
`pc.make_poms_call(method="launch_jobs", ...)`, which raised a `RuntimeError`
with an effectively empty message (see `docs/poms_client_gotchas.md`'s
`if res.find("Traceback"):` bug — it destroys the real response body on
every non-303 response before `submit_next_slice()` ever sees it). The real
body, recovered by manually bypassing `make_poms_call()`, was
`AssertionError('No more splits in this campaign.')` — the campaign stage's
Input Dataset was genuinely exhausted. That's expected, normal completion,
not a failure worth erroring on every hour — but the code had no way to tell
it apart from a real failure (bad auth, bad params, an outage), since the
one signal that would distinguish them never survived the trip through
`make_poms_call()`.

## The fix

`submit_next_slice()` no longer calls `pc.make_poms_call()`. It calls
`raw_poms_call(pc, "launch_jobs", ...)` (see "Update" below), which
reimplements `make_poms_call()`'s auth+POST logic mirrored line-for-line
minus the buggy formatting branch, so it sees the real status code and body.
The 303 success path is unchanged. A non-303 body containing
`"No more splits in this campaign"` logs an INFO line and returns `None`.
Any other non-303 raises `RuntimeError` with the real body. `run()` in
`poms_auto_submit.py` treats `None` as "stop submitting further slices this
run," without advancing or persisting `last_split`.

## Consequence

`PomsSession`'s stated contract (CONTEXT.md: "Decision logic never sees `pc`
or a raw POMS response") now has one deliberate, narrowly-scoped exception —
`submit_next_slice()` itself sees the raw body to recognize this one server
string. Its own callers still only ever see `submission_id`, `None`, or a
`RuntimeError`, never the raw text.

## Accepted tradeoff (original)

This duplicates ~15 lines of `make_poms_call()`'s auth/POST plumbing rather
than patching the vendored (CVMFS-installed, not ours to patch) library or
filing an upstream bug. If `poms_client`'s auth mechanism changes upstream,
this copy needs updating in lockstep — the same risk already accepted for
`test/debug_raw_call.py`'s existing copy, now extended into production code.
Judged acceptable: the duplicated logic is small, stable auth plumbing (not
business logic), and already independently proven correct by two prior
copies (`make_poms_call()` itself, `debug_raw_call.py`).

## Update (2026-09-02): the duplication was collapsed, not accepted

An architecture review flagged this tradeoff itself: two independently
hand-copied implementations of the same auth+POST plumbing (this one, and
`debug_raw_call.py`'s pre-existing copy) is exactly the "two adapters justify
a seam" case, not a one-off worth duplicating. `raw_poms_call(pc, method,
**kwargs)` now lives in `scripts/poms_raw_client.py` as the one
implementation; `PomsSession.submit_next_slice()` and `test/debug_raw_call.py`
both call it instead of each maintaining their own copy. The "Accepted
tradeoff" above is superseded by this — kept for the historical reasoning,
not as current guidance.
