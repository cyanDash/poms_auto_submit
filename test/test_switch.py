import sys

import poms_auto_submit as psc


CONFIG_TEMPLATE = """[poms]
experiment = sbnd
role = production
campaign_name = test_campaign
campaign_stage_name = test_stage

[decision]
switch = {switch}
pct_complete_threshold = 80
submit_two_slices = 0
max_splits = 5
last_split = 0

[paths]
log_file = test.log
"""


def make_config_file(tmp_path, switch):
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_TEMPLATE.format(switch=switch))
    return config_path


def test_load_config_switch_defaults_true_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("POMS_CLIENT_DIR", str(tmp_path))
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_TEMPLATE.replace("switch = {switch}\n", "").format(switch=""))

    cfg = psc.load_config(str(config_path))

    assert cfg["switch"] is True


def test_main_skips_run_when_switch_off(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("POMS_CLIENT_DIR", str(tmp_path))
    config_path = make_config_file(tmp_path, switch=0)

    def fail_if_called(cfg, dry_run):
        raise AssertionError("run() should not be called when switch is off")

    monkeypatch.setattr(psc, "run", fail_if_called)
    monkeypatch.setattr(sys, "argv", ["poms_auto_submit.py", "--config", str(config_path)])

    with caplog.at_level("INFO"):
        result = psc.main()

    assert result == 0
    assert "switch is off" in caplog.text


def test_main_calls_run_when_switch_on(tmp_path, monkeypatch):
    monkeypatch.setenv("POMS_CLIENT_DIR", str(tmp_path))
    config_path = make_config_file(tmp_path, switch=1)
    monkeypatch.setattr(psc, "LOCK_FILE", str(tmp_path / "test.lock"))

    calls = []
    monkeypatch.setattr(psc, "run", lambda cfg, dry_run: calls.append((cfg, dry_run)))
    monkeypatch.setattr(sys, "argv", ["poms_auto_submit.py", "--config", str(config_path)])

    result = psc.main()

    assert result == 0
    assert len(calls) == 1
