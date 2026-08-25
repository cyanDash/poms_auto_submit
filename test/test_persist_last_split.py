import poms_auto_submit as psc

CONFIG_TEXT = """[poms]
experiment = sbnd
role = analysis
campaign_name = test_campaign
campaign_stage_name = test_stage

[decision]
pct_complete_threshold = 80
; keep this comment
submit_two_slices = 0
max_splits = 5
last_split = 0

[paths]
log_file = poms_auto_submit.log
"""


def test_persist_last_split_updates_value_in_place(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(CONFIG_TEXT)

    psc.persist_last_split(str(config_path), 3)

    updated = config_path.read_text()
    assert "last_split = 3" in updated
    assert "max_splits = 5" in updated
    assert "; keep this comment" in updated


def test_persist_last_split_missing_key_raises(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[decision]\npct_complete_threshold = 80\n")

    try:
        psc.persist_last_split(str(config_path), 1)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
