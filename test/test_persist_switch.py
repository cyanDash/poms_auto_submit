import poms_auto_submit as psc

CONFIG_TEXT = """[poms]
experiment = sbnd
campaign_name = test_campaign
campaign_stage_name = test_stage

[decision]
switch = 1
; keep this comment
max_splits = 5
last_split = 0
"""


def test_persist_switch_updates_value_in_place(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_TEXT)

    psc.persist_switch(str(config_path), False)

    updated = config_path.read_text()
    assert "switch = 0" in updated
    assert "max_splits = 5" in updated
    assert "; keep this comment" in updated


def test_persist_switch_missing_key_raises(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[decision]\npct_complete_threshold = 80\n")

    try:
        psc.persist_switch(str(config_path), True)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
