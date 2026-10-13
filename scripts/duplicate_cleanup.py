"""duplicate_cleanup: retires the duplicate output files of a Campaign
Stage, keeping one worker's chain of outputs per input across its Output
Definitions. Files are only ever retired: no location is removed and nothing
on disk is deleted. See docs/adr/0021.

Public: clean_definitions() is the entry point (plan or resume, retire,
verify); plan_cleanup() and verify_plan() are its two read-only halves;
main() is the command line. Everything else is private.

By hand: python scripts/duplicate_cleanup.py --dataset A [--dataset B ...] [--dry-run]
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
# Files named in one failure message; the plan file lists them all.
MAX_FILES_NAMED = 20


def _batches(names, size):
    """`names`, deduplicated and sorted, in lists of at most `size`."""
    names = sorted(set(names))
    for start in range(0, len(names), size):
        yield names[start:start + size]


def _input_key(name):
    """Group key for a root ancestor: a data file's name is cut at the '-'
    that starts the hash after its stream name; other names are kept whole."""
    strm = name.find("strm")
    if strm < 0:
        return name
    dash = name.find("-", strm)
    return name if dash < 0 else name[:dash]


def _fetch_metadata(sam, names, known):
    """Adds {name: {process_id, parents}} to `known` for every name SAM
    knows and `known` lacks."""
    for batch in _batches(set(names) - set(known), METADATA_BATCH_SIZE):
        for md in sam.getMultipleMetadata(batch):
            known[md["file_name"]] = {
                "process_id": md.get("process_id"),
                "parents": [p["file_name"] for p in md.get("parents") or []],
            }


def _live_names(sam, names):
    """The subset of `names` that SAM lists as live files."""
    live = set()
    for batch in _batches(names, LIVE_CHECK_BATCH_SIZE):
        live.update(sam.listFiles(dimensions="file_name " + ",".join(batch)))
    return live


def _ancestor_chains(sam, files, depth, known):
    """({file: [first parent, its first parent, ...] of length `depth`},
    {file: why the chain stops early})."""
    chains = {f: [] for f in files}
    current = {f: f for f in files}
    failed = {}
    for _ in range(depth):
        _fetch_metadata(sam, current.values(), known)
        above = {}
        for f, name in current.items():
            if name not in known:
                failed[f] = f"no metadata in SAM for {name}"
            elif not known[name]["parents"]:
                failed[f] = f"{name} has no parent"
            else:
                above[f] = known[name]["parents"][0]
                chains[f].append(above[f])
        current = above
    return {f: chains[f] for f in current}, failed


def _depth_fault(chain, depth, definition_of, depth_of, ungrouped):
    """Why a file's recorded `depth` does not lead to an input, or None.
    `chain` is its ancestors, nearest first."""
    if not chain:
        return f"depth {depth} does not lead to an input"
    root = chain[-1]
    if root in definition_of:
        return f"depth {depth} does not lead to an input: it reaches {root} of {definition_of[root]}"
    for steps, ancestor in enumerate(chain[:-1], start=1):
        if ancestor in ungrouped:
            return f"its ancestor {ancestor} could not be grouped"
        if ancestor in definition_of and depth_of[definition_of[ancestor]] != depth - steps:
            return (
                f"depth {depth} disagrees with depth {depth_of[definition_of[ancestor]]} "
                f"of its ancestor {ancestor} in {definition_of[ancestor]}"
            )
    return None


def _kept_files(group, known, definition_of, covered):
    """(the files of `group` to keep, the kept process id). The kept worker
    is the process id whose files and their descendants cover the most
    definitions, lowest id on a tie. When a file has no process id the id is
    None and the first file by name is kept in each definition."""
    by_pid = defaultdict(set)
    for f in group:
        pid = known[f]["process_id"]
        if pid is None:
            first = {}
            for name in sorted(group):
                first.setdefault(definition_of[name], name)
            return set(first.values()), None
        by_pid[pid] |= covered(f)
    kept_pid = min(by_pid, key=lambda pid: (-len(by_pid[pid]), pid))
    return {f for f in group if known[f]["process_id"] == kept_pid}, kept_pid


def plan_cleanup(definitions, sam):
    """The cleanup plan for `definitions` ([{name, depth}]), read from `sam`
    (listFiles, getMultipleMetadata, getFileLineage); nothing is changed.

    Per input, one worker is kept among the files nearest the input, then
    one among the files under each kept file, and so on down; everything
    under a file that is not kept is retired with it.

    Returns {"definitions": [...], "inputs": {...}, "fallback_used": bool}.
    "definitions" is deepest first, each {name, depth, retire, skipped,
    ungrouped, totals}. "inputs" maps each input to {kept_process_id,
    fallback, kept, retired, skipped}, the last three keyed by definition.
    A plan with any `ungrouped` file must not be applied.
    """
    ordered = sorted(definitions, key=lambda d: (-d["depth"], d["name"]))
    depth_of = {d["name"]: d["depth"] for d in ordered}
    known = {}
    report = {}
    listed = {}
    definition_of = {}
    for d in ordered:
        listed[d["name"]] = sorted(sam.listFiles(defname=d["name"]))
        report[d["name"]] = {
            "name": d["name"], "depth": d["depth"],
            "retire": [], "skipped": [], "ungrouped": [],
            "totals": {"files": len(listed[d["name"]]), "kept": 0},
        }
        definition_of.update({f: d["name"] for f in listed[d["name"]]})

    # Shallowest first, so a file's ancestors are grouped before it is.
    ungrouped = set()
    input_of = {}
    by_input = defaultdict(list)
    under = defaultdict(list)
    for d in reversed(ordered):
        chains, failed = _ancestor_chains(sam, listed[d["name"]], d["depth"], known)
        for f in listed[d["name"]]:
            reason = failed.get(f) or _depth_fault(chains[f], d["depth"], definition_of, depth_of, ungrouped)
            if reason:
                ungrouped.add(f)
                report[d["name"]]["ungrouped"].append({"file": f, "reason": reason})
                continue
            input_of[f] = _input_key(chains[f][-1])
            parent = next((a for a in chains[f][:-1] if a in definition_of), None)
            if parent is None:
                by_input[input_of[f]].append(f)
            else:
                under[parent].append(f)

    covers = {}

    def covered(f):
        if f not in covers:
            covers[f] = {definition_of[f]}.union(*(covered(child) for child in under[f]))
        return covers[f]

    inputs = {}
    candidates = defaultdict(list)

    def retire_with_descendants(f, reason):
        row = {"file": f, "input": input_of[f], "process_id": known[f]["process_id"]}
        candidates[definition_of[f]].append({**row, "reason": reason} if reason else row)
        for child in under[f]:
            retire_with_descendants(child, f"parent {f} is retired")

    for key in sorted(by_input):
        plan_input = {"kept_process_id": None, "fallback": False, "kept": {}, "retired": {}, "skipped": {}}
        inputs[key] = plan_input
        groups = [(None, by_input[key])]
        while groups:
            parent, group = groups.pop()
            kept, kept_pid = _kept_files(group, known, definition_of, covered)
            if parent is None:
                plan_input["kept_process_id"] = kept_pid
                reason = None
            elif kept_pid is None:
                reason = f"under {parent}, first file by name kept"
            else:
                reason = f"under {parent}, kept process id {kept_pid}"
            plan_input["fallback"] = plan_input["fallback"] or kept_pid is None
            for f in sorted(group):
                if f in kept:
                    plan_input["kept"].setdefault(definition_of[f], []).append(f)
                    report[definition_of[f]]["totals"]["kept"] += 1
                    if under[f]:
                        groups.append((f, under[f]))
                else:
                    retire_with_descendants(f, reason)
        plan_input["kept"] = {name: sorted(files) for name, files in sorted(plan_input["kept"].items())}

    # Deepest first, so a file's children are settled before the file is.
    retiring = set()
    for d in ordered:
        entry = report[d["name"]]
        rows = sorted(candidates[d["name"]], key=lambda row: (row["input"], row["file"]))
        children = {
            row["file"]: [c["file_name"] for c in sam.getFileLineage("children", row["file"])]
            for row in rows
        }
        # A retired child does not hold its parent back.
        live = _live_names(sam, {c for cs in children.values() for c in cs if c not in retiring})
        for row in rows:
            f, key = row["file"], row["input"]
            blocking = sorted(c for c in children[f] if c in live)
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


def _plan_path(cfg):
    """<cache_dir>/cleanup_plan_<ini name>.json, next to the State File."""
    state_name = os.path.splitext(os.path.basename(cfg["state_file"]))[0]
    ini_name = state_name[len("state_"):] if state_name.startswith("state_") else state_name
    return os.path.join(cfg["cache_dir"], f"cleanup_plan_{ini_name}.json")


def _write_plan(cfg, plan):
    """Writes `plan` to its plan file and returns the path."""
    path = _plan_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(plan, f, indent=2)
    os.replace(tmp_path, path)
    return path


def _ensure_retired_files_definition(sam, definition):
    """Creates `<definition>_retired_files` unless SAM already has it."""
    name = f"{definition}_retired_files"
    dimensions = f"dataset.tag {definition} and availability: retired"
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


def _retire_reason(row, plan):
    why = row.get("reason") or "kept process id %s" % plan["inputs"][row["input"]]["kept_process_id"]
    return f"input {row['input']}, {why}"


def _apply_definition(entry, plan, sam, dry_run):
    """Retires one definition's planned files, then makes sure its
    retired-files definition exists. Returns None, or the first failure as
    {definition, file, error}."""
    name = entry["name"]
    f = None
    try:
        live = set(sam.listFiles(defname=name)) if entry["retire"] else set()
        for row in entry["retire"]:
            f = row["file"]
            reason = _retire_reason(row, plan)
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


def _apply_plan(cfg, plan, sam, dry_run):
    """Retires the plan's files, one definition after another in the plan's
    order, stopping at the first failure. Each finished definition is
    recorded in the State File's `cleanup` section and skipped from then
    on. Returns None, or the failure as {definition, file, error}. A dry
    run only logs."""
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


def verify_plan(plan, sam):
    """Checks `sam` against an applied plan: per input and definition the
    live files are the kept ones plus those the children guard skipped, and
    every kept file's parents are live. Returns {input: [what is wrong]},
    empty when all is well."""
    names = [d["name"] for d in plan["definitions"]]
    live = {name: set(sam.listFiles(defname=name)) for name in names}
    kept_files = [f for i in plan["inputs"].values() for files in i["kept"].values() for f in files]
    known = {}
    _fetch_metadata(sam, kept_files, known)
    live_parents = _live_names(sam, [p for f in kept_files if f in known for p in known[f]["parents"]])

    bad = defaultdict(list)
    for key, plan_input in plan["inputs"].items():
        for name in names:
            for f in plan_input["kept"].get(name, []):
                if f not in live[name]:
                    bad[key].append(f"{name}: kept file {f} is not live")
                for p in known.get(f, {}).get("parents", []):
                    if p not in live_parents:
                        bad[key].append(f"{name}: parent {p} of kept file {f} is not live")
            for f in plan_input["retired"].get(name, []):
                if f in live[name]:
                    bad[key].append(f"{name}: {f} is live but not kept")

    for entry in plan["definitions"]:
        name = entry["name"]
        planned = set()
        for plan_input in plan["inputs"].values():
            planned.update(plan_input["kept"].get(name, []))
            planned.update(plan_input["retired"].get(name, []))
            planned.update(s["file"] for s in plan_input["skipped"].get(name, []))
        extra = sorted(live[name] - planned)
        chains, failed = _ancestor_chains(sam, extra, entry["depth"], {}) if extra else ({}, {})
        for f in extra:
            if f in failed:
                bad[f].append(f"{name}: {f} is live but not in the plan ({failed[f]})")
            else:
                bad[_input_key(chains[f][-1])].append(f"{name}: {f} is live but not in the plan")
    return dict(bad)


def _ungrouped_error(plan, path):
    """Error text naming the plan's ungrouped files, or None."""
    rows = [(entry["name"], u) for entry in plan["definitions"] for u in entry["ungrouped"]]
    if not rows:
        return None
    named = "; ".join(f"{u['file']} in {name} ({u['reason']})" for name, u in rows[:MAX_FILES_NAMED])
    more = f"; and {len(rows) - MAX_FILES_NAMED} more" if len(rows) > MAX_FILES_NAMED else ""
    return (
        f"{len(rows)} file(s) cannot be grouped under an input, nothing retired "
        f"(all listed in {path}): {named}{more}"
    )


def _load_unfinished_plan(cfg, definitions):
    """(plan, path) of the plan a past run started applying and has not
    finished, or (None, None). Raises RuntimeError when that plan cannot be
    read or is for other definitions."""
    progress = state.read(cfg, "cleanup") or {}
    path = progress.get("plan_path")
    if not path or progress.get("finished"):
        return None, None
    logging.info("cleanup: resuming the unfinished plan %s", path)
    try:
        with open(path) as f:
            plan = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"cannot read the saved plan {path}: {e}") from e
    planned = sorted(d["name"] for d in plan["definitions"])
    asked = sorted(d["name"] for d in definitions)
    if planned != asked:
        raise RuntimeError(f"the unfinished plan {path} is for {', '.join(planned)}, not {', '.join(asked)}")
    return plan, path


def clean_definitions(cfg, definitions, sam, dry_run=False):
    """Cleans the duplicates of `definitions` ([{name, depth}]) through
    `sam`: resumes the plan an earlier run left unfinished, or makes a new
    one and writes its plan file; retires its files; verifies the result. A
    plan with a file that cannot be grouped under an input fails before
    anything is retired. A dry run writes the plan file, logs what would be
    retired and changes nothing else: no SAM change, no State File.

    Returns {"ok", "plan_path", "failure", "bad_inputs", "skipped"}.
    `failure` is None, or {definition, file, error} for what stopped the
    run. `bad_inputs` is verify_plan()'s answer. `skipped` lists the files
    left in place because they have live children outside the plan, as
    {definition, input, file}; they do not fail verification.
    """
    result = {"ok": False, "plan_path": None, "failure": None, "bad_inputs": {}, "skipped": []}

    def failed(error):
        result["failure"] = {"definition": None, "file": None, "error": str(error)}
        return result

    try:
        plan, path = _load_unfinished_plan(cfg, definitions)
        resumed = plan is not None
        if not resumed:
            plan = plan_cleanup(definitions, sam)
            path = _write_plan(cfg, plan)
            logging.info("cleanup: plan written to %s", path)
        result["plan_path"] = path
    except Exception as e:
        logging.exception("cleanup: planning failed")
        return failed(e)

    ungrouped = _ungrouped_error(plan, path)
    if ungrouped:
        logging.error("cleanup: %s", ungrouped)
        return failed(ungrouped)
    if not (resumed or dry_run):
        state.write(cfg, "cleanup", {"plan_path": path, "done": [], "finished": False})

    result["skipped"] = [
        {"definition": entry["name"], "input": row["input"], "file": row["file"]}
        for entry in plan["definitions"] for row in entry["skipped"]
    ]
    for row in result["skipped"]:
        logging.info(
            "cleanup: %s from %s left in place, has children outside the plan (input %s)",
            row["file"], row["definition"], row["input"],
        )

    result["failure"] = _apply_plan(cfg, plan, sam, dry_run)
    if result["failure"]:
        return result
    if dry_run:
        result["ok"] = True
        return result

    try:
        result["bad_inputs"] = verify_plan(plan, sam)
    except Exception as e:
        logging.exception("cleanup: verification could not be done")
        return failed(e)
    for key, reasons in result["bad_inputs"].items():
        logging.error("cleanup: verification failed for input %s: %s", key, "; ".join(reasons))
    if result["bad_inputs"]:
        return result
    logging.info("cleanup: verified %d input(s)", len(plan["inputs"]))
    state.write(cfg, "cleanup", {**state.read(cfg, "cleanup", {}), "finished": True})
    result["ok"] = True
    return result


def main(argv=None, sam=None):
    """Command line for running by hand. Every Output Definition is taken
    at depth 1. The plan file and the State File go in the working
    directory. Returns the exit code."""
    parser = argparse.ArgumentParser(
        description="Retire duplicate output files in SAM, keeping one worker's outputs per input.",
    )
    parser.add_argument("-s", "--dataset", action="append", required=True,
                        help="Output Definition to clean; repeat for every Output Definition of the Campaign Stage")
    parser.add_argument("-D", "--dry-run", action="store_true",
                        help="write the plan file and log what would be retired; "
                             "change nothing in SAM and write no State File")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if sam is None:
        import sam_client
        sam = sam_client.get_sam_client(os.environ.get("SAM_EXPERIMENT", "sbnd"))
    cache_dir = os.getcwd()
    cfg = {"cache_dir": cache_dir, "state_file": os.path.join(cache_dir, "state_duplicate_cleanup.json")}
    definitions = [{"name": name, "depth": 1} for name in dict.fromkeys(args.dataset)]
    result = clean_definitions(cfg, definitions, sam, dry_run=args.dry_run)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
