# poms_auto_submit

Hourly cron script that checks a POMS campaign stage's progress and submits the next slice when it's ready. This context is small and single-purpose: decision logic (when/how much to submit) plus one seam out to POMS.

## Language

**Campaign**:
A named group of one or more Campaign Stages in POMS. `poms_auto_submit` only ever watches one Campaign Stage per run, identified by `campaign_name` + `campaign_stage_name` in `config.ini` — but Submission concurrency is capped per Campaign, not per Campaign Stage: this script limits how many Submissions are active (New/Idle/Running) across the *whole* Campaign at once, not just the stage it's watching. A busy Campaign Stage elsewhere in the same Campaign holds back this stage's next Slice too.

**Campaign Stage**:
One node in a Campaign's processing pipeline, identified by `campaign_stage_id`/`name`, configured via `param_overrides` (a list of `[key, value]` pairs). In general a Campaign Stage can consume an earlier Campaign Stage's output as its Input Dataset, so a Campaign can chain several of them (e.g. a separate gen stage feeding a separate reco stage). Current SBND production convention avoids that: everything runs as a single Campaign Stage containing multiple Executables (gen→g4→detsim→reco1→reco2→caf), because chaining stages means copying output files back to dCache and back to a worker node for the next stage, which has lost files before. Running one Campaign Stage keeps files on the worker node the whole way through.
_Avoid_: "stage" alone when a Campaign Stage's internal Executables are the actual topic — say Executable.

**Executable**:
One step (`gen`, `g4`, `detsim`, `reco1`, `reco2`, `caf`, ...) run inside a single Campaign Stage under the current one-Campaign-Stage-per-pipeline convention. Configured by one `-Oglobal.fclfileN=` param override per Executable (`fclfile1`..`fclfile6` for a 6-step chain).

**PomsSession**:
The seam to `poms_client`. Constructed once per run from `(pc, cfg)`; owns identity setup (`update_session_experiment`/`update_session_role`), resolves and caches `campaign_stage_id`, and normalizes every quirky `poms_client` response shape ((ok, data) tuples, bare strings, redirect URLs with the id in the query string) behind plain method returns. Decision logic never sees `pc` or a raw POMS response.
_Avoid_: PomsGateway, poms_client wrapper.

**Submission**:
POMS's general term for one `launch_jobs` call against a campaign stage. Has a `submission_id`, a Status, and `pct_complete`. This script makes no code-level distinction between a Submission and a Slice — mechanically it just launches jobs per the stage's configured parameters, for any campaign.

**Status**:
A Submission's state in POMS: `New`, `Idle`, `Running`, `Held` (requested more grid resources than it's allowed), `Located`, `Finished`, `Cancelled` (manually cancelled by a user), `Removed`. Fixed set, established POMS/forms vocabulary. Decision logic only reads `pct_complete` off `Running` Submissions; `New`/`Idle`/`Running` together count as "active" for the per-Campaign concurrency cap (see Campaign).

**Slice**:
A Submission that processes one batch of a campaign stage's Input Dataset, because the grid can only run so many jobs at once (e.g. 50,000 input files, 1,000 jobs per batch → 5 slices). Whether a given Submission is genuinely a Slice is a matter of production convention, not code: `production`-role campaigns always build an Input Dataset (even for from-scratch generation — see Input Dataset), so their Submissions are true Slices; `analysis`-role campaigns typically don't bother with one, so "Slice" there is habitual/loose terminology for "Submission."
_Avoid_: using "Slice" and "Submission" as if they were always identical — see above.

**Input Dataset**:
The SAM dataset of fcl files a campaign stage consumes as input, dividing its total work into batches. Even a campaign generating events entirely from scratch (no real input data) still builds one under production convention: each fcl file has no event data, just a run/subrun/event-number baked in, which the stage's executables read as that batch's identity. This is why `production`-role campaigns always have an Input Dataset and `analysis`-role campaigns typically don't.

**Subgroup**:
The `pro`/`standard` priority lane a slice's jobs run in, set via the `-Osubmit.subgroup=` param override. Only the `production` role may hold `pro`, and only one slice at a time.

**Role**:
An experiment member's submission privilege level in POMS, set per-run via `cfg["role"]`. `analysis` is the default every experiment member has. `production` is a restricted privilege held only by people trusted to run submissions that produce datasets for the whole experiment to use — it carries priority (see Subgroup) and is the convention under which an Input Dataset always gets built (see Input Dataset).
