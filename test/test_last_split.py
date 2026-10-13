import poms_auto_submit as psc
import state

CONFIG_TEMPLATE = """[poms]
experiment = sbnd
campaign_name = test_campaign
campaign_stage_name = test_stage

[decision]
pct_complete_threshold = 80
max_splits = 5
{extra}
"""


def load(tmp_path, monkeypatch, extra="", name="campaign_a.ini"):
    # load_config() puts the campaign dir at <ini dir>/../logs/<campaign_name>;
    # a configs/ subdir keeps that inside tmp_path.
    monkeypatch.setenv("POMS_CLIENT_DIR", str(tmp_path))
    config_path = tmp_path / "configs" / name
    config_path.parent.mkdir(exist_ok=True)
    config_path.write_text(CONFIG_TEMPLATE.format(extra=extra))
    return psc.load_config(str(config_path))


def test_last_split_starts_at_zero_with_no_ini_key_and_no_state(tmp_path, monkeypatch):
    assert load(tmp_path, monkeypatch)["last_split"] == 0


def test_state_file_is_named_after_the_ini(tmp_path, monkeypatch):
    cfg = load(tmp_path, monkeypatch)

    assert cfg["state_file"].endswith("logs/test_campaign/state_campaign_a.json")


def test_two_inis_for_one_campaign_get_separate_state(tmp_path, monkeypatch):
    first = load(tmp_path, monkeypatch, name="campaign_a.ini")
    psc.persist_last_split(first, 4)

    second = load(tmp_path, monkeypatch, name="campaign_a_recovery.ini")

    assert second["state_file"] != first["state_file"]
    assert second["last_split"] == 0


def test_persisted_last_split_is_loaded_on_the_next_run(tmp_path, monkeypatch):
    psc.persist_last_split(load(tmp_path, monkeypatch), 3)

    assert load(tmp_path, monkeypatch)["last_split"] == 3


def test_old_ini_last_split_seeds_until_first_write(tmp_path, monkeypatch):
    assert load(tmp_path, monkeypatch, extra="last_split = 7")["last_split"] == 7


def test_state_file_value_wins_over_a_stale_ini_value(tmp_path, monkeypatch):
    psc.persist_last_split(load(tmp_path, monkeypatch, extra="last_split = 7"), 9)

    assert load(tmp_path, monkeypatch, extra="last_split = 7")["last_split"] == 9


def test_persist_last_split_leaves_other_state_untouched(tmp_path, monkeypatch):
    cfg = load(tmp_path, monkeypatch)
    state.write(cfg, "recovery_handled", True)

    psc.persist_last_split(cfg, 1)

    assert state.read(cfg, "recovery_handled") is True
