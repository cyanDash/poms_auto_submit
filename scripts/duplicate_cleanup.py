"""duplicate_cleanup: decides which duplicate output files of a Campaign
Stage to retire, keeping one worker's outputs per input across all of its
output definitions. plan_cleanup() only reads SAM, through the client it is
handed; write_plan() saves the result as a report. See issue #16.
"""

import json
import os
from collections import defaultdict

METADATA_BATCH_SIZE = 500


def _input_key(name):
    """Group key for a root ancestor: a data file's name is cut at the '-'
    that starts the hash after its stream name; other names are kept whole."""
    strm = name.find("strm")
    if strm < 0:
        return name
    dash = name.find("-", strm)
    return name if dash < 0 else name[:dash]


def _fetch_metadata(sam, names, known):
    """Adds {name: {process_id, parent}} to `known` for every name SAM
    knows; `parent` is the first parent's name, or None."""
    missing = sorted(set(names) - set(known))
    for start in range(0, len(missing), METADATA_BATCH_SIZE):
        for md in sam.getMultipleMetadata(missing[start:start + METADATA_BATCH_SIZE]):
            parents = md.get("parents") or []
            known[md["file_name"]] = {
                "process_id": md.get("process_id"),
                "parent": parents[0]["file_name"] if parents else None,
            }


def _root_ancestors(sam, files, depth, known):
    """{file: (ancestor name, None) or (None, reason)}, the ancestor being
    `depth` first-parent steps above the file."""
    current = {f: f for f in files}
    failed = {}
    for _ in range(depth):
        _fetch_metadata(sam, current.values(), known)
        above = {}
        for f, name in current.items():
            if name not in known:
                failed[f] = f"no metadata in SAM for {name}"
            elif known[name]["parent"] is None:
                failed[f] = f"{name} has no parent"
            else:
                above[f] = known[name]["parent"]
        current = above
    result = {f: (name, None) for f, name in current.items()}
    result.update({f: (None, reason) for f, reason in failed.items()})
    return result


def _kept_process_id(by_definition, known):
    """The process id with files in the most definitions, lowest id on a
    tie; None when any file has no process id."""
    present = defaultdict(set)
    for name, files in by_definition.items():
        for f in files:
            pid = known[f]["process_id"]
            if pid is None:
                return None
            present[pid].add(name)
    return min(present, key=lambda pid: (-len(present[pid]), pid))


def plan_cleanup(definitions, sam):
    """The cleanup plan for `definitions` ([{name, depth}]), read from `sam`
    (listFiles, getMultipleMetadata, getFileLineage); nothing is changed.

    Returns {"definitions": [...], "inputs": {...}, "fallback_used": bool}.
    "definitions" is deepest first, each {name, depth, retire, skipped,
    ungrouped, totals}. "inputs" maps each input to {kept_process_id,
    fallback, kept, retired, skipped}, the last three keyed by definition.
    """
    ordered = sorted(definitions, key=lambda d: (-d["depth"], d["name"]))
    known = {}
    by_input = defaultdict(lambda: defaultdict(list))
    report = {}
    for d in ordered:
        files = sorted(sam.listFiles(defname=d["name"]))
        entry = {
            "name": d["name"], "depth": d["depth"],
            "retire": [], "skipped": [], "ungrouped": [],
            "totals": {"files": len(files), "kept": 0},
        }
        report[d["name"]] = entry
        ancestors = _root_ancestors(sam, files, d["depth"], known)
        for f in files:
            ancestor, reason = ancestors[f]
            if ancestor is None:
                entry["ungrouped"].append({"file": f, "reason": reason})
            else:
                by_input[_input_key(ancestor)][d["name"]].append(f)

    inputs = {}
    candidates = defaultdict(list)
    for key in sorted(by_input):
        by_definition = by_input[key]
        kept_pid = _kept_process_id(by_definition, known)
        plan_input = {
            "kept_process_id": kept_pid, "fallback": kept_pid is None,
            "kept": {}, "retired": {}, "skipped": {},
        }
        inputs[key] = plan_input
        for name in sorted(by_definition):
            files = by_definition[name]
            if kept_pid is None:
                kept = files[:1]
            else:
                kept = [f for f in files if known[f]["process_id"] == kept_pid]
            if kept:
                plan_input["kept"][name] = kept
                report[name]["totals"]["kept"] += len(kept)
            candidates[name] += [(f, key) for f in files if f not in kept]

    # Deepest first, so a file's children are settled before the file is.
    retiring = set()
    for d in ordered:
        entry = report[d["name"]]
        for f, key in candidates[d["name"]]:
            children = [c["file_name"] for c in sam.getFileLineage("children", f)]
            blocking = sorted(c for c in children if c not in retiring)
            row = {"file": f, "input": key, "process_id": known[f]["process_id"]}
            if blocking:
                reason = "has children that are not being retired: " + ", ".join(blocking)
                entry["skipped"].append({**row, "reason": reason})
                inputs[key]["skipped"].setdefault(d["name"], []).append({"file": f, "reason": reason})
            else:
                retiring.add(f)
                entry["retire"].append(row)
                inputs[key]["retired"].setdefault(d["name"], []).append(f)
        entry["totals"].update(
            retired=len(entry["retire"]), skipped=len(entry["skipped"]),
            ungrouped=len(entry["ungrouped"]),
        )

    return {
        "definitions": [report[d["name"]] for d in ordered],
        "inputs": inputs,
        "fallback_used": any(i["fallback"] for i in inputs.values()),
    }


def plan_path(cfg):
    """<cache_dir>/cleanup_plan_<ini name>.json, next to the State File."""
    state_name = os.path.splitext(os.path.basename(cfg["state_file"]))[0]
    ini_name = state_name[len("state_"):] if state_name.startswith("state_") else state_name
    return os.path.join(cfg["cache_dir"], f"cleanup_plan_{ini_name}.json")


def write_plan(cfg, plan):
    """Writes `plan` to plan_path(cfg) and returns that path."""
    path = plan_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(plan, f, indent=2)
    os.replace(tmp_path, path)
    return path
