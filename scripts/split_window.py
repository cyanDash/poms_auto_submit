"""Builds the `new(...)` cs_split_type string for one launch: the ini's
[split] section fixes everything but `firsttime`, which steps by `step` per
launch. See docs/adr/0024-rewrite-new-split-firsttime-per-launch.md."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DURATION_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
# Operators write times in this zone (CDT/CST) regardless of the host's TZ.
USER_TZ = ZoneInfo("America/Chicago")

# localtime defaults to 0: POMS then renders firsttime/lasttime as GMT in the
# SAM create_date dimension, and SAM reads that bare timestamp as UTC. With
# localtime=1 POMS renders Central time that SAM still reads as UTC, so the
# window lands 5 (CDT) or 6 (CST) hours early. The unix times we send are
# true instants either way; see docs/adr/0024.
DEFAULTS = {"window": "1h", "round": "1", "fts": "1800", "localtime": "0", "lasttime": "2031-04-09"}


def parse_duration(text):
    """Seconds in `text`: an integer with an optional w/d/h/m/s suffix."""
    text = text.strip()
    unit = DURATION_UNITS.get(text[-1:])
    return int(text[:-1] if unit else text) * (unit or 1)


def _unix(text):
    """Central-time `YYYY-MM-DD[ HH:MM]` to unix seconds."""
    fmt = "%Y-%m-%d %H:%M" if " " in text.strip() else "%Y-%m-%d"
    return int(datetime.strptime(text.strip(), fmt).replace(tzinfo=USER_TZ).timestamp())


def read(parser):
    """The [split] section as a dict, or None when the ini has none."""
    if not parser.has_section("split"):
        return None
    split = {key: parser.get("split", key, fallback=default) for key, default in DEFAULTS.items()}
    split["first_start"] = parser.get("split", "first_start")
    split["step"] = parser.get("split", "step")
    return split


def read_combine(parser):
    """(first_launch, last_launch) of the ini's [combine] section, or None.
    One launch then takes the windows of launches first..last together;
    see docs/adr/0028."""
    if not parser.has_section("combine"):
        return None
    first, last = parser.getint("combine", "first_launch"), parser.getint("combine", "last_launch")
    if last < first:
        raise ValueError(f"[combine] last_launch {last} is before first_launch {first}")
    return first, last


def _firsttime(split, n):
    return _unix(split["first_start"]) + n * parse_duration(split["step"])


def window_start(split, n):
    """Unix time (UTC) of the first input file launch `n` takes: the
    `firsttime` of its cs_split_type, and the epoch in the window's SAM
    definition name `..._time_<first>__<last>`."""
    return _firsttime(split, n)


def describe(split, n):
    """The Central-time range of input files launch `n` takes."""
    start = datetime.fromtimestamp(_firsttime(split, n), timezone.utc)
    end = start + timedelta(seconds=parse_duration(split["window"]))
    local_start, local_end = start.astimezone(USER_TZ), end.astimezone(USER_TZ)
    return f"{local_start:%Y-%m-%d %H:%M} to {local_end:%Y-%m-%d %H:%M} {local_end:%Z}"


def build_split_type(split, n):
    """cs_split_type for launch number `n` (0-based)."""
    firsttime = _firsttime(split, n)
    return (
        f"new(window={split['window']},round={split['round']},fts={split['fts']},"
        f"localtime={split['localtime']},firsttime={firsttime},lasttime={_unix(split['lasttime'])})"
    )


def window_end(split, n):
    """Unix time (UTC) of the end of the input files launch `n` takes."""
    return _firsttime(split, n) + parse_duration(split["window"])


def window_dimensions(split, n, dataset):
    """SAM dimensions of the files launch `n` takes from `dataset`: created
    after the window's start and up to its end (UTC, as POMS reads them)."""
    def stamp(unix):
        return datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    return f"defname: {dataset} and create_date > '{stamp(window_start(split, n))}' and create_date <= '{stamp(window_end(split, n))}'"


def _stamp(unix):
    return datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def combined_dimensions(split, first, last, dataset):
    """SAM dimensions of the files launches `first` to `last` (inclusive)
    take from `dataset`: the union of their windows, each cut the way
    window_dimensions() cuts one. Not the span between them, which would
    also hold every hour the windows leave out."""
    parts = " or ".join(
        f"(create_date > '{_stamp(window_start(split, n))}' and create_date <= '{_stamp(window_end(split, n))}')"
        for n in range(first, last + 1)
    )
    return f"defname: {dataset} and ({parts})"


def combined_name(split, first, last, dataset):
    """Name of the combined definition. Ends `_time_<first>__<last>` like
    POMS's window definitions, so the plotter reads its epochs."""
    return f"{dataset}_combined_time_{window_start(split, first)}__{window_end(split, last)}"
