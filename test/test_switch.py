import sys

import poms_auto_submit as psc

CONFIG_TEMPLATE = """[poms]
experiment = sbnd
campaign_name = test_campaign
campaign_stage_name = test_stage

[decision]
pct_complete_threshold = 80
submit_two_slices = 0
max_splits = 5
{extra}
"""


def make_config_file(tmp_path, monkeypatch, extra=""):
    # load_config() puts the campaign dir at <ini dir>/../logs/<campaign_name>;
    # a configs/ subdir keeps that inside tmp_path.
    monkeypatch.setenv("POMS_CLIENT_DIR", str(tmp_path))
    config_path = tmp_path / "configs" / "config.ini"
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(CONFIG_TEMPLATE.format(extra=extra))
    return config_path


def run_main(config_path, monkeypatch):
    calls = []
    monkeypatch.setattr(psc, "run", lambda cfg, dry_run: calls.append((cfg, dry_run)))
    monkeypatch.setattr(sys, "argv", ["poms_auto_submit.py", "--config", str(config_path)])
    return psc.main(), calls


def test_switch_is_on_with_no_ini_key_and_no_state(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, monkeypatch)

    assert psc.load_config(str(config_path))["switch"] is True


def test_main_calls_run_when_switch_on(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, monkeypatch)

    result, calls = run_main(config_path, monkeypatch)

    assert result == 0
    assert len(calls) == 1


def test_main_skips_run_once_switch_is_persisted_off(tmp_path, monkeypatch, caplog):
    config_path = make_config_file(tmp_path, monkeypatch)
    psc.persist_switch(psc.load_config(str(config_path)), False)

    with caplog.at_level("INFO"):
        result, calls = run_main(config_path, monkeypatch)

    assert result == 0
    assert calls == []
    assert "switch is off" in caplog.text


def test_persist_switch_never_touches_the_ini(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, monkeypatch)
    before = config_path.read_text()

    psc.persist_switch(psc.load_config(str(config_path)), False)

    assert config_path.read_text() == before


def test_old_ini_switch_off_is_honoured_until_state_has_a_value(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, monkeypatch, extra="switch = 0")

    result, calls = run_main(config_path, monkeypatch)

    assert result == 0
    assert calls == []


def test_state_file_switch_wins_over_a_stale_ini_value(tmp_path, monkeypatch):
    config_path = make_config_file(tmp_path, monkeypatch, extra="switch = 0")
    psc.persist_switch(psc.load_config(str(config_path)), True)

    _, calls = run_main(config_path, monkeypatch)

    assert len(calls) == 1
