"""Builds the `new(...)` cs_split_type string for one launch: the ini's
[split] section fixes everything but `firsttime`, which steps by `step` per
launch. See docs/adr/0023-rewrite-new-split-firsttime-per-launch.md."""

from datetime import datetime
from zoneinfo import ZoneInfo

DURATION_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
# Operators write times in this zone (CDT/CST) regardless of the host's TZ.
USER_TZ = ZoneInfo("America/Chicago")

# localtime defaults to 0: POMS then renders firsttime/lasttime as GMT in the
# SAM create_date dimension, and SAM reads that bare timestamp as UTC. With
# localtime=1 POMS renders Central time that SAM still reads as UTC, so the
# window lands 5 (CDT) or 6 (CST) hours early. The unix times we send are
# true instants either way; see docs/adr/0023.
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


def build_split_type(split, n):
    """cs_split_type for launch number `n` (0-based)."""
    firsttime = _unix(split["first_start"]) + n * parse_duration(split["step"])
    return (
        f"new(window={split['window']},round={split['round']},fts={split['fts']},"
        f"localtime={split['localtime']},firsttime={firsttime},lasttime={_unix(split['lasttime'])})"
    )
