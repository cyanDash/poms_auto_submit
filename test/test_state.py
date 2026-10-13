import json

import state


def cfg(tmp_path):
    return {"state_file": str(tmp_path / "state.json")}


def test_read_returns_default_when_nothing_written(tmp_path):
    assert state.read(cfg(tmp_path), "stragglers", {}) == {}


def test_write_then_read_round_trips_one_section(tmp_path):
    state.write(cfg(tmp_path), "recovery_handled", True)

    assert state.read(cfg(tmp_path), "recovery_handled") is True


def test_write_leaves_other_sections_untouched(tmp_path):
    state.write(cfg(tmp_path), "stragglers", {"1": {"stagger_count": 2}})
    state.write(cfg(tmp_path), "recovery_handled", True)

    assert json.loads((tmp_path / "state.json").read_text()) == {
        "stragglers": {"1": {"stagger_count": 2}},
        "recovery_handled": True,
    }


def test_write_leaves_no_temp_file_behind(tmp_path):
    state.write(cfg(tmp_path), "switch", False)

    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_malformed_file_reads_as_empty(tmp_path):
    (tmp_path / "state.json").write_text("{not json")

    assert state.read(cfg(tmp_path), "stragglers", {}) == {}


def test_no_state_file_reads_default_and_writes_nothing(tmp_path):
    state.write({}, "recovery_handled", True)

    assert state.read({}, "recovery_handled", False) is False
    assert list(tmp_path.iterdir()) == []
