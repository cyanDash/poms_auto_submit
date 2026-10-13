"""duplicate_cleanup: retires the duplicate output files of a Campaign
Stage, keeping one worker's outputs per input across all of its output
definitions. clean_definitions() is the entry point: it plans (or resumes
an unfinished plan), retires in SAM and verifies, all through the SAM
client it is handed. plan_cleanup() only reads SAM; write_plan() saves the
plan as a report. Files are only ever retired: no location is removed and
nothing on disk is deleted. See issue #16.

By hand: python scripts/duplicate_cleanup.py --dataset A [--dataset B ...] [--dry_run]
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict

import state

METADATA_BATCH_SIZE = 500
LIVE_CHECK_BATCH_SIZE = 200


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


def _live_files(sam, definition):
    """The definition's files that are not retired."""
    return set(sam.listFiles(defname=definition))


def retired_files_definition(definition):
    """(name, dimensions) of the definition holding `definition`'s retired
    files."""
    return f"{definition}_retired_files", f"dataset.tag {definition} and availability: retired"


def _ensure_retired_files_definition(sam, definition):
    """Creates the retired-files definition unless SAM already has it."""
    name, dimensions = retired_files_definition(definition)
    try:
        sam.descDefinitionDict(name)
    except Exception as e:
        # samweb_client's own exception, matched by name so this module
        # imports without samweb_client.
        if type(e).__name__ != "DefinitionNotFound":
            raise
        sam.createDefinition(name, dimensions)
        logging.info("cleanup: created definition %s (%s)", name, dimensions)
    else:
        logging.info("cleanup: definition %s already exists -- left as it is", name)


def _apply_definition(entry, plan, sam, dry_run):
    """Retires one definition's planned files, then records them in its
    retired-files definition. Returns None, or the first failure as
    {definition, file, error}."""
    name = entry["name"]
    f = None
    try:
        live = _live_files(sam, name) if entry["retire"] else set()
        for row in entry["retire"]:
            f = row["file"]
            reason = "input %s, kept process id %s" % (
                row["input"], plan["inputs"][row["input"]]["kept_process_id"],
            )
            if f not in live:
                logging.info("cleanup: %s from %s is already retired (%s)", f, name, reason)
            elif dry_run:
                logging.info("cleanup: dry run: would retire %s from %s (%s)", f, name, reason)
            else:
                sam.retireFile(f)
                logging.info("cleanup: retired %s from %s (%s)", f, name, reason)
        f = None
        if entry["retire"] and not dry_run:
            _ensure_retired_files_definition(sam, name)
    except Exception as e:
        if f is None:
            logging.exception("cleanup: %s failed", name)
        else:
            logging.exception("cleanup: retiring %s from %s failed", f, name)
        return {"definition": name, "file": f, "error": str(e)}
    return None


def apply_plan(cfg, plan, sam, dry_run=False):
    """Retires the plan's files in `sam`, one definition after another in
    the plan's order (deepest first), stopping at the first failure. Each
    finished definition is recorded in the State File's `cleanup` section
    and skipped from then on. Returns None, or the failure as
    {definition, file, error}. A dry run only logs."""
    progress = state.read(cfg, "cleanup") or {}
    done = list(progress.get("done", []))
    for entry in plan["definitions"]:
        if entry["name"] in done:
            logging.info("cleanup: %s already cleaned -- skipped", entry["name"])
            continue
        failure = _apply_definition(entry, plan, sam, dry_run)
        if failure:
            return failure
        if not dry_run:
            done.append(entry["name"])
            state.write(cfg, "cleanup", {**progress, "done": done})
    return None


def _live_names(sam, names):
    """The subset of `names` that SAM lists as live files."""
    names = sorted(set(names))
    live = set()
    for start in range(0, len(names), LIVE_CHECK_BATCH_SIZE):
        batch = names[start:start + LIVE_CHECK_BATCH_SIZE]
        live.update(sam.listFiles(dimensions="file_name " + ",".join(batch)))
    return live


def _parents(sam, files):
    """{file: [parent names]} for every file SAM knows."""
    files = sorted(files)
    parents = {}
    for start in range(0, len(files), METADATA_BATCH_SIZE):
        for md in sam.getMultipleMetadata(files[start:start + METADATA_BATCH_SIZE]):
            parents[md["file_name"]] = [p["file_name"] for p in md.get("parents") or []]
    return parents


def verify_plan(plan, sam):
    """Checks `sam` against an applied plan: per input and definition the
    live files are the kept ones plus those the children guard skipped, and
    every kept file's parents are live. Returns {input: [what is wrong]},
    empty when all is well."""
    names = [d["name"] for d in plan["definitions"]]
    live = {name: _live_files(sam, name) for name in names}
    kept_files = [f for i in plan["inputs"].values() for files in i["kept"].values() for f in files]
    parents = _parents(sam, kept_files)
    live_parents = _live_names(sam, [p for ps in parents.values() for p in ps])

    bad = defaultdict(list)
    for key, plan_input in plan["inputs"].items():
        for name in names:
            kept = plan_input["kept"].get(name, [])
            for f in kept:
                if f not in live[name]:
                    bad[key].append(f"{name}: kept file {f} is not live")
                for p in parents.get(f, []):
                    if p not in live_parents:
                        bad[key].append(f"{name}: parent {p} of kept file {f} is not live")
            for f in plan_input["retired"].get(name, []):
                if f in live[name]:
                    bad[key].append(f"{name}: {f} is live but not kept")

    for entry in plan["definitions"]:
        name = entry["name"]
        planned = {u["file"] for u in entry["ungrouped"]}
        for plan_input in plan["inputs"].values():
            planned.update(plan_input["kept"].get(name, []))
            planned.update(plan_input["retired"].get(name, []))
            planned.update(s["file"] for s in plan_input["skipped"].get(name, []))
        extra = sorted(live[name] - planned)
        ancestors = _root_ancestors(sam, extra, entry["depth"], {}) if extra else {}
        for f in extra:
            ancestor, reason = ancestors[f]
            if ancestor is None:
                bad[f].append(f"{name}: {f} is live but not in the plan ({reason})")
            else:
                bad[_input_key(ancestor)].append(f"{name}: {f} is live but not in the plan")
    return dict(bad)


def _skipped_files(plan):
    """The files the children guard left in place, as [{definition, input,
    file}] in the plan's order."""
    return [
        {"definition": entry["name"], "input": row["input"], "file": row["file"]}
        for entry in plan["definitions"] for row in entry["skipped"]
    ]


def _unfinished_plan_path(cfg):
    """Path of the plan a past run started applying and has not finished,
    or None."""
    progress = state.read(cfg, "cleanup") or {}
    if progress.get("plan_path") and not progress.get("finished"):
        return progress["plan_path"]
    return None


def clean_definitions(cfg, definitions, sam, dry_run=False):
    """Cleans the duplicates of `definitions` ([{name, depth}]) through
    `sam`: resumes the plan an earlier run left unfinished, or makes and
    writes a new one; retires its files; verifies the result. Files are
    only retired -- no location is removed and nothing on disk is deleted.
    A dry run writes the plan, logs what would be retired and changes
    nothing.

    Returns {"ok", "dry_run", "plan_path", "failure", "bad_inputs",
    "skipped", "skipped_count"}. `bad_inputs` is verify_plan()'s answer.
    `failure` is None, or {definition, file, error} for what stopped the
    run. `skipped` lists the files left in place because they have children
    outside the plan, as {definition, input, file}; they do not fail
    verification.
    """
    result = {
        "ok": False, "dry_run": dry_run, "plan_path": None, "failure": None, "bad_inputs": {},
        "skipped": [], "skipped_count": 0,
    }
    try:
        path = _unfinished_plan_path(cfg)
        if path:
            logging.info("cleanup: resuming the unfinished plan %s", path)
            try:
                with open(path) as f:
                    plan = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(f"cannot read the saved plan {path}: {e}") from e
            planned = sorted(d["name"] for d in plan["definitions"])
            asked = sorted(d["name"] for d in definitions)
            if planned != asked:
                raise RuntimeError(
                    f"the unfinished plan {path} is for {', '.join(planned)}, not {', '.join(asked)}"
                )
        else:
            plan = plan_cleanup(definitions, sam)
            path = write_plan(cfg, plan)
            logging.info("cleanup: plan written to %s", path)
            if not dry_run:
                state.write(cfg, "cleanup", {"plan_path": path, "done": [], "finished": False})
        result["plan_path"] = path
    except Exception as e:
        logging.exception("cleanup: planning failed")
        result["failure"] = {"definition": None, "file": None, "error": str(e)}
        return result

    result["skipped"] = _skipped_files(plan)
    result["skipped_count"] = len(result["skipped"])
    for row in result["skipped"]:
        logging.info(
            "cleanup: %s from %s left in place, has children outside the plan (input %s)",
            row["file"], row["definition"], row["input"],
        )

    result["failure"] = apply_plan(cfg, plan, sam, dry_run)
    if result["failure"]:
        return result
    if dry_run:
        result["ok"] = True
        return result

    try:
        result["bad_inputs"] = verify_plan(plan, sam)
    except Exception as e:
        logging.exception("cleanup: verification could not be done")
        result["failure"] = {"definition": None, "file": None, "error": str(e)}
        return result
    for key, reasons in result["bad_inputs"].items():
        logging.error("cleanup: verification failed for input %s: %s", key, "; ".join(reasons))
    if result["bad_inputs"]:
        return result
    logging.info("cleanup: verified %d input(s)", len(plan["inputs"]))
    state.write(cfg, "cleanup", {**state.read(cfg, "cleanup", {}), "finished": True})
    result["ok"] = True
    return result


def main(argv=None, sam=None):
    """Command line for running by hand; every dataset is taken as a direct
    child of the input (depth 1). The plan and the progress file go in the
    working directory. Returns the exit code."""
    parser = argparse.ArgumentParser(
        description="Retire duplicate output files in SAM, keeping one worker's outputs per input.",
    )
    parser.add_argument("-s", "--dataset", action="append", required=True,
                        help="SAM definition to clean; repeat for every output definition of the stage")
    parser.add_argument("-D", "--dry_run", action="store_true",
                        help="write the plan and log what would be retired; change nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sam is None:
        import samweb_client
        sam = samweb_client.SAMWebClient(experiment=os.environ.get("SAM_EXPERIMENT", "sbnd"))
    cache_dir = os.getcwd()
    cfg = {"cache_dir": cache_dir, "state_file": os.path.join(cache_dir, "state_duplicate_cleanup.json")}
    definitions = [{"name": name, "depth": 1} for name in dict.fromkeys(args.dataset)]
    result = clean_definitions(cfg, definitions, sam, dry_run=args.dry_run)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
