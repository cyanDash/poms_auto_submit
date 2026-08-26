Hourly cron script that submits POMS production slices for one SBND campaign
stage. Small (~400 line), TDD-built, config-driven. Start here, then go deeper
as needed:

- **README.md** — what it does, how to set up (`source setup.sh`), configure
  (`configs/config.ini`), and run (dry-run → real run → crontab).
- **docs/adr/** — why past decisions were made (e.g. dropping the
  campaign-wide concurrency cap, defaulting new slices to the `pro`
  subgroup, running as `sbndpro` with managed tokens instead of `kinit`).
- **docs/poms_client_gotchas.md** — read this *before* touching
  `scripts/poms_session.py`. It explains real `poms_client` response shapes
  and upstream bugs that several lines in that file exist to route around;
  without it, those lines look like they could be simplified but can't.

@CONTEXT.md

## Testing

`pytest test/` is fast and offline — `configs/pytest.ini` excludes the `live`
marker by default. `pytest test/ -m live` runs `test/test_live_campaign.py`
against real production POMS: needs `source setup.sh` first (real auth), and
is read-only by design (no submit/param-update calls in the automated live
suite — see `docs/poms_client_gotchas.md` for why a write path is riskier to
automate).

## Caution: this hits real production infrastructure

`submit_next_slice()` launches real grid jobs; `update_stage_params()`
changes a live campaign stage's params. Always validate with `--dry-run`
first when testing against a real campaign, per the README's example
workflow.

## Convention: campaign branches never merge back into main

Each real production campaign runs off its own branch carrying a
campaign-specific `configs/config.ini` (different `campaign_name`,
`max_splits`, etc.). Those branches are never merged into `main`.
