"""state: the one on-disk file of internal state per config,
<cache_dir>/state_<ini name>.json (cfg["state_file"]), one top-level section
per owner. See docs/adr/0020-one-consolidated-state-file-per-config.md."""

import json
import os


def _load(cfg):
    path = cfg.get("state_file")
    if not path:
        return {}
    try:
        with open(path) as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def read(cfg, section, default=None):
    """One section's value, or `default` when it was never written, the file
    is malformed, or cfg has no state_file."""
    return _load(cfg).get(section, default)


def write(cfg, section, value):
    """Replace one section, leaving the others as they are on disk. No-op
    when cfg has no state_file."""
    path = cfg.get("state_file")
    if not path:
        return
    state = _load(cfg)
    state[section] = value
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp_path, path)
