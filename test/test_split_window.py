import configparser
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import split_window
import state
from helpers import make_cfg
from poms_auto_submit import submit_plan
from poms_session import EmptyDataset, LaunchFailed

SPLIT = {
    "window": "1h", "round": "1", "fts": "1800", "localtime": "0",
    "first_start": "2026-10-23 00:00", "step": "2h", "lasttime": "2031-04-09",
}


def unix(text, fmt="%Y-%m-%d %H:%M"):
    return int(datetime.strptime(text, fmt).replace(tzinfo=ZoneInfo("America/Chicago")).timestamp())


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
        "new(window=1h,round=1,fts=1800,localtime=0,"
        f"firsttime={unix('2026-10-23 ' + start)},lasttime={unix('2031-04-09', '%Y-%m-%d')})"
    )

    assert split_window.build_split_type(SPLIT, n) == expected


def test_read_returns_none_without_a_split_section():
    assert split_window.read(configparser.ConfigParser()) is None


def test_read_fills_defaults_and_requires_first_start_and_step():
    parser = configparser.ConfigParser()
    parser.read_string("[split]\nfirst_start = 2026-10-23 00:00\nstep = 1d\n")

    split = split_window.read(parser)

    assert (split["window"], split["round"], split["fts"], split["localtime"]) == ("1h", "1", "1800", "0")
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


def test_first_start_is_central_time_whatever_the_host_tz(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    # 2026-10-22 00:00 CDT is 05:00 UTC.
    assert "firsttime=1792645200" in split_window.build_split_type({**SPLIT, "first_start": "2026-10-22 00:00"}, 0)


def test_describe_gives_the_central_time_range():
    assert split_window.describe(SPLIT, 1) == "2026-10-23 02:00 to 2026-10-23 03:00 CDT"


def test_describe_follows_the_window_across_midnight():
    split = {**SPLIT, "first_start": "2026-10-23 22:00", "window": "3h"}

    assert split_window.describe(split, 0).startswith("2026-10-23 22:00 to 2026-10-24 01:00 CDT")


def test_submit_plan_logs_the_input_window_of_each_launch(monkeypatch, caplog):
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: None)
    caplog.set_level("INFO")

    submit_plan(make_cfg(split=SPLIT, last_split=1), FakeSession(), [False, False])

    messages = [r.getMessage() for r in caplog.records]
    for n in (1, 2):
        assert any(f"launch {n}" in m and split_window.describe(SPLIT, n) in m for m in messages)


def test_submit_plan_stops_without_counting_a_launch_failed_submission(monkeypatch, caplog):
    persisted = []
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: persisted.append(n))
    caplog.set_level("INFO")

    class FailingSession(FakeSession):
        def submit_next_slice(self):
            self.calls.append(("submit",))
            raise LaunchFailed("submission 77 is LaunchFailed")

    cfg = make_cfg(split=SPLIT, last_split=1)
    session = FailingSession()

    assert submit_plan(cfg, session, [False, False]) is True

    assert [c for c in session.calls if c[0] == "submit"] == [("submit",)]
    assert cfg["last_split"] == 1 and persisted == []
    assert any(r.levelname == "ERROR" and "LaunchFailed" in r.getMessage() for r in caplog.records)


def test_submit_plan_counts_an_empty_window_and_moves_on(monkeypatch, caplog):
    persisted = []
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: persisted.append(n))
    caplog.set_level("INFO")
    outcomes = iter([EmptyDataset("submission 77 had no input files"), "78"])

    class EmptyThenOk(FakeSession):
        def submit_next_slice(self):
            self.calls.append(("submit",))
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    cfg = make_cfg(split=SPLIT, last_split=1)
    session = EmptyThenOk()

    assert submit_plan(cfg, session, [False, False]) is True

    assert [c[0] for c in session.calls].count("submit") == 2
    assert cfg["last_split"] == 3 and persisted == [2, 3]
    assert any("no input files" in r.getMessage() and split_window.describe(SPLIT, 1) in r.getMessage() for r in caplog.records)


def test_window_start_is_the_utc_epoch_of_the_launchs_first_input_file():
    assert split_window.window_start(SPLIT, 1) == unix("2026-10-23 02:00")


def test_submit_plan_records_each_launchs_submission_under_its_window_start(tmp_path):
    cfg = make_cfg(split=SPLIT, last_split=1, state_file=str(tmp_path / "state.json"))

    submit_plan(cfg, FakeSession(), [False, False])

    assert state.read(cfg, "window_launches") == {
        str(unix("2026-10-23 02:00")): {"submission_id": "1"},
        str(unix("2026-10-23 04:00")): {"submission_id": "1"},
    }


def test_submit_plan_keeps_earlier_window_records(tmp_path):
    cfg = make_cfg(split=SPLIT, last_split=1, state_file=str(tmp_path / "state.json"))
    state.write(cfg, "window_launches", {"1": {"submission_id": "0"}})

    submit_plan(cfg, FakeSession(), [False])

    assert state.read(cfg, "window_launches")["1"] == {"submission_id": "0"}


def test_submit_plan_without_split_records_no_window(tmp_path):
    cfg = make_cfg(state_file=str(tmp_path / "state.json"))

    submit_plan(cfg, FakeSession(), [False])

    assert state.read(cfg, "window_launches") is None


@pytest.mark.parametrize("outcome", [EmptyDataset("x"), LaunchFailed("x"), None])
def test_submit_plan_records_nothing_when_no_submission_came_back(tmp_path, outcome):
    class Session(FakeSession):
        def submit_next_slice(self):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    cfg = make_cfg(split=SPLIT, last_split=1, state_file=str(tmp_path / "state.json"))

    submit_plan(cfg, Session(), [False])

    assert state.read(cfg, "window_launches") is None


class CountingSam:
    """countFiles() answers by window start (unix), 0 unless listed."""

    def __init__(self, counts):
        self.counts, self.asked = counts, []

    def countFiles(self, dimensions):
        self.asked.append(dimensions)
        return next((n for start, n in self.counts.items() if f"> '{start}'" in dimensions), 0)


def test_window_dimensions_are_the_utc_range_of_the_launch():
    assert split_window.window_dimensions(SPLIT, 1, "ds") == (
        "defname: ds and create_date > '2026-10-23T07:00:00+00:00' and create_date <= '2026-10-23T08:00:00+00:00'"
    )


def test_submit_plan_skips_empty_windows_without_launching(monkeypatch, caplog):
    persisted = []
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: persisted.append(n))
    caplog.set_level("INFO")
    sam = CountingSam({"2026-10-23T09:00:00+00:00": 5})  # launch 2 only
    cfg = make_cfg(split=SPLIT, last_split=0)
    session = FakeSession()

    assert submit_plan(cfg, session, [False], sam=sam, dataset="ds", now=unix("2026-11-01 04:00")) is True

    assert [c[1] for c in session.calls if c[0] == "set_split_type"] == [split_window.build_split_type(SPLIT, 2)]
    assert cfg["last_split"] == 3 and persisted == [1, 2, 3]
    assert any("counting it without launching" in r.getMessage() for r in caplog.records)


def test_submit_plan_does_not_count_a_window_still_open(monkeypatch):
    persisted = []
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: persisted.append(n))
    cfg = make_cfg(split=SPLIT, last_split=1)
    session = FakeSession()
    sam = CountingSam({})

    # Launch 1 is 02:00-03:00 CDT on 2026-10-23; it is 02:30.
    assert submit_plan(cfg, session, [False], sam=sam, dataset="ds", now=unix("2026-10-23 02:30")) is True

    assert session.calls == [] and sam.asked == [] and persisted == []


def test_submit_plan_launches_when_sam_cannot_be_asked(monkeypatch):
    monkeypatch.setattr("poms_auto_submit.persist_last_split", lambda cfg, n: None)

    class BrokenSam:
        def countFiles(self, dimensions):
            raise OSError("sam down")

    session = FakeSession()
    submit_plan(make_cfg(split=SPLIT, last_split=1), session, [False], sam=BrokenSam(), dataset="ds", now=unix("2026-11-01 04:00"))

    assert ("submit",) in session.calls


# --- [combine]: one launch for several windows (ADR-0028) ---

class CombineSession(FakeSession):
    def __init__(self, result="55"):
        super().__init__()
        self.result = result

    def set_input_dataset(self, name):
        self.calls.append(("dataset", name))

    def submit_next_slice(self):
        self.calls.append(("submit",))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class MakingSam(CountingSam):
    def __init__(self, count=88):
        super().__init__({})
        self.count, self.made = count, []

    def countFiles(self, dimensions):
        self.asked.append(dimensions)
        return self.count

    def descDefinition(self, name):
        raise KeyError(name)

    def createDefinition(self, name, dims):
        self.made.append((name, dims))


def combine_cfg(tmp_path, **kw):
    return make_cfg(split=SPLIT, combine=(1, 3), last_split=1, state_file=str(tmp_path / "state.json"), **kw)


NOW = unix("2026-11-01 04:00")


def test_combined_dimensions_union_the_windows_not_the_span():
    dims = split_window.combined_dimensions(SPLIT, 1, 2, "ds")

    assert dims == (
        "defname: ds and ((create_date > '2026-10-23T07:00:00+00:00' and create_date <= '2026-10-23T08:00:00+00:00')"
        " or (create_date > '2026-10-23T09:00:00+00:00' and create_date <= '2026-10-23T10:00:00+00:00'))"
    )
    assert split_window.combined_name(SPLIT, 1, 2, "ds") == f"ds_combined_time_{unix('2026-10-23 02:00')}__{unix('2026-10-23 05:00')}"


def test_combined_launch_is_pro_takes_the_union_and_moves_last_split_past_it(tmp_path):
    cfg, session, sam = combine_cfg(tmp_path), CombineSession(), MakingSam()
    name = split_window.combined_name(SPLIT, 1, 3, "ds")

    assert submit_plan(cfg, session, [False], sam=sam, dataset="ds", now=NOW) is True

    assert session.calls == [
        ("set_subgroup", True), ("dataset", name), ("set_split_type", ""), ("submit",),
        ("dataset", "ds"), ("set_split_type", split_window.build_split_type(SPLIT, 4)),
    ]
    assert sam.made == [(name, split_window.combined_dimensions(SPLIT, 1, 3, "ds"))]
    assert cfg["last_split"] == 4 and state.read(cfg, "last_split") == 4
    launches = state.read(cfg, "window_launches")
    assert launches == {str(split_window.window_start(SPLIT, n)): {"submission_id": "55"} for n in (1, 2, 3)}


def test_combined_launch_waits_while_its_last_window_is_open(tmp_path):
    cfg, session, sam = combine_cfg(tmp_path), CombineSession(), MakingSam()

    submit_plan(cfg, session, [False], sam=sam, dataset="ds", now=unix("2026-10-23 05:30"))

    assert session.calls == [] and cfg["last_split"] == 1 and sam.asked == []


def test_combined_launch_without_files_is_counted_and_not_launched(tmp_path):
    cfg, session = combine_cfg(tmp_path), CombineSession()

    submit_plan(cfg, session, [False], sam=MakingSam(count=0), dataset="ds", now=NOW)

    assert session.calls == [] and cfg["last_split"] == 4


def test_failed_combined_launch_restores_the_stage_and_is_not_counted(tmp_path):
    cfg, session = combine_cfg(tmp_path), CombineSession(result=LaunchFailed("x"))

    assert submit_plan(cfg, session, [False], sam=MakingSam(), dataset="ds", now=NOW) is True

    assert cfg["last_split"] == 1 and state.read(cfg, "window_launches") is None
    assert session.calls[-2:] == [("dataset", "ds"), ("set_split_type", split_window.build_split_type(SPLIT, 4))]


def test_launches_outside_the_combined_range_are_normal(tmp_path):
    cfg = combine_cfg(tmp_path)
    cfg["last_split"] = 4
    session = CombineSession()

    submit_plan(cfg, session, [False], sam=MakingSam(), dataset="ds", now=NOW)

    assert ("dataset", split_window.combined_name(SPLIT, 1, 3, "ds")) not in session.calls
    assert cfg["last_split"] == 5


def test_read_combine_reads_the_section_and_rejects_a_backwards_range():
    parser = configparser.ConfigParser()
    assert split_window.read_combine(parser) is None
    parser.read_string("[combine]\nfirst_launch = 7\nlast_launch = 10\n")
    assert split_window.read_combine(parser) == (7, 10)
    parser.read_string("[combine]\nfirst_launch = 7\nlast_launch = 6\n")
    with pytest.raises(ValueError):
        split_window.read_combine(parser)
