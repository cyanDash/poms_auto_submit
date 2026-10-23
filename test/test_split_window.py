import configparser
import time
from datetime import datetime

import pytest

import split_window
from helpers import make_cfg
from poms_auto_submit import submit_plan

SPLIT = {
    "window": "1h", "round": "1", "fts": "1800", "localtime": "1",
    "first_start": "2026-10-23 00:00", "step": "2h", "lasttime": "2031-04-09",
}


def unix(text, fmt="%Y-%m-%d %H:%M"):
    return int(time.mktime(datetime.strptime(text, fmt).timetuple()))


@pytest.mark.parametrize("text,seconds", [
    ("30s", 30), ("5m", 300), ("2h", 7200), ("1d", 86400), ("1w", 604800), ("90", 90),
])
def test_parse_duration_understands_wdhms_suffixes(text, seconds):
    assert split_window.parse_duration(text) == seconds


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        split_window.parse_duration("soon")


@pytest.mark.parametrize("n,start", [(0, "00:00"), (1, "02:00"), (2, "04:00")])
def test_build_split_type_steps_firsttime_by_step_per_launch(n, start):
    expected = (
        "new(window=1h,round=1,fts=1800,localtime=1,"
        f"firsttime={unix('2026-10-23 ' + start)},lasttime={unix('2031-04-09', '%Y-%m-%d')})"
    )

    assert split_window.build_split_type(SPLIT, n) == expected


def test_read_returns_none_without_a_split_section():
    assert split_window.read(configparser.ConfigParser()) is None


def test_read_fills_defaults_and_requires_first_start_and_step():
    parser = configparser.ConfigParser()
    parser.read_string("[split]\nfirst_start = 2026-10-23 00:00\nstep = 1d\n")

    split = split_window.read(parser)

    assert (split["window"], split["round"], split["fts"], split["localtime"]) == ("1h", "1", "1800", "1")
    assert split["lasttime"] == "2031-04-09"

    parser.read_string("[split]\nstep = 1d\n")
    parser.remove_option("split", "first_start")
    with pytest.raises(configparser.NoOptionError):
        split_window.read(parser)


class FakeSession:
    def __init__(self):
        self.calls = []

    def set_split_type(self, split_type):
        self.calls.append(("set_split_type", split_type))

    def set_subgroup(self, use_pro):
        self.calls.append(("set_subgroup", use_pro))

    def submit_next_slice(self):
        self.calls.append(("submit",))
        return "1"


def test_submit_plan_sets_split_type_before_each_submit(monkeypatch):
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: None)
    cfg = make_cfg(split=SPLIT, last_split=1)
    session = FakeSession()

    submit_plan(cfg, session, [False, False])

    kinds = [c[0] for c in session.calls]
    assert kinds == ["set_subgroup", "set_split_type", "submit"] * 2
    assert [c[1] for c in session.calls if c[0] == "set_split_type"] == [
        split_window.build_split_type(SPLIT, 1),
        split_window.build_split_type(SPLIT, 2),
    ]


def test_submit_plan_without_split_never_touches_split_type(monkeypatch):
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: None)
    session = FakeSession()

    submit_plan(make_cfg(), session, [False])

    assert ("set_split_type",) not in [c[:1] for c in session.calls]
    assert all(c[0] != "set_split_type" for c in session.calls)
