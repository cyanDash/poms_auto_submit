"""output_definitions: infers a Campaign Stage's Output Definitions and is
the only writer of the State File's `output_definitions` section.
get_output_definitions() is the module's main interface; it raises
OutputDefinitionsError. trace_output_definitions() is the read-only view of
the same inference: its steps and the tags each keeps or drops, which
get_output_definitions() also logs at INFO. See docs/adr/0023.
"""

import logging
import re
from fnmatch import fnmatchcase

import sam_client
import state

SECTION = "output_definitions"

# Fixed constants, not config knobs. An Input Dataset of at most
# LINEAGE_MAX_PROBED_INPUTS files is read in full; a larger one is probed
# at every k-th file (k = size // LINEAGE_MAX_PROBED_INPUTS) until
# LINEAGE_SAMPLE_SIZE inputs with children were read. A candidate name is
# checked on VERIFY_SAMPLE_FILES of its files.
LINEAGE_SAMPLE_SIZE = 20
LINEAGE_MAX_PROBED_INPUTS = 200
LINEAGE_MAX_DEPTH = 6
VERIFY_SAMPLE_FILES = 5

# scripts/run_recovery.sh names the Recovery Dataset
# <input>_<campaign_name>_recovery_campaign.
RECOVERY_DATASET_SUFFIX = "_{campaign_name}_recovery_campaign"

POSTSCRIPT_PATH_PATTERN = re.compile(r"-f\s+dropbox://(\S*metadata_postscripts[^/\s]*\.sh)")
# The postscript a job runs: ". ${CONDOR_DIR_INPUT}/<name>.sh ..." in --postscript-unquote.
POSTSCRIPT_RUN_PATTERN = re.compile(r"\$\{?CONDOR_DIR_INPUT\}?/(metadata_postscripts[^/\s]*\.sh)")
MT_EXPORT_PATTERN = re.compile(r"export\s+(MT_\w+)=(\S+)")
# The definition POMS made for the launch: on this branch one `new()` window
# of the Input Dataset (docs/adr/0025).
LAUNCH_DATASET_PATTERN = re.compile(r"--dataset_definition=['\"]?([^\s'\"]+)")
VERSION_PATTERN = re.compile(r"--mdappversion[\s=]+(\S+)")
SETUP_VERSION_PATTERN = re.compile(r"--setup-unquote\s+(?:sbndcode|icaruscode)\s+(v\S+)")
# The postscripts name the software version this, and never assign it.
VERSION_VARIABLE = "SBNDCODE_VERSION"
PRODUCTION_NAME_VARIABLE = "MT_PRODUCTIONNAME"
PROJECT_VERSION_VARIABLE = "MT_PROJECTVERSION"
SAMPLE_VARIABLE = "MT_SAMPLE"
TEST_PREFIX = "test_"
# A Test Launch writes outputs under the same inputs with tags like these.
TEST_TAG_PATTERN = re.compile(r"^test\d*_")

DTAG_ASSIGNMENT_PATTERN = re.compile(r"^[ \t]*(?:export[ \t]+)?(\w*DTAG\w*)=(\S*)", re.MULTILINE)
SHELL_VARIABLE_PATTERN = re.compile(r"\$\{(\w+)\}|\$(\w+)")

TRACE_PREFIX = "output definitions: step"
STEP_NAMES = (
    "launch command", "input dataset", "lineage tags", "test launch tags",
    "postscript patterns", "candidates", "verification", "classification", "result",
)
# Metadata fields a candidate's files must not contradict, and the launch
# command value each is compared with; a field absent on either side is skipped.
METADATA_FIELDS = (("production.name", "production_name"), ("sbnd_project.version", "version"))


class OutputDefinitionsError(RuntimeError):
    """The output definitions could not be inferred; nothing was recorded."""


def recorded_output_definitions(cfg):
    """The Output Definitions the State File holds as [{name, depth}]; empty
    when the section is unfilled or an old flat list of names. Only reads."""
    recorded = state.read(cfg, SECTION, []) or []
    if recorded and all(isinstance(entry, dict) for entry in recorded):
        return [dict(entry) for entry in recorded]
    return []


def get_output_definitions(cfg, session, sam, dry_run=False):
    """The Campaign Stage's Output Definitions as [{name, depth}]: what the
    State File holds, else inferred and recorded. A flat list of names is
    never trusted: it is inferred again and replaced. A dry run records
    nothing. The inference logs its steps at INFO. Raises
    OutputDefinitionsError when the definitions cannot be inferred; the
    State File is then left as it is."""
    recorded = recorded_output_definitions(cfg)
    if recorded:
        return recorded

    trace = _trace(cfg, session, sam, "not recorded (dry run)" if dry_run else "recorded")
    if trace["error"]:
        raise OutputDefinitionsError(trace["error"])
    if not dry_run:
        state.write(cfg, SECTION, trace["definitions"])
    return trace["definitions"]


def trace_output_definitions(cfg, session, sam):
    """The inference without recording or raising: {steps, rows,
    definitions, error}. `steps` are the steps, each {step, name,
    summary, keeps, drops} (entries {tag, depth, detail, reason}); `rows`
    is describe_lineage_tags()'s; `error` is None or why nothing would be
    recorded. Only reads."""
    return _trace(cfg, session, sam, "inferred, not recorded (read-only)")


def render_step(step, multiline=False):
    """One step as the INFO line (prefix included) or, with `multiline`, as
    an indented section."""
    head = f"step {step['step']} {step['name']}: {step['summary']}"
    keeps = [_render_entry(e) for e in step["keeps"]]
    drops = [_render_entry(e) for e in step["drops"]]
    if multiline:
        lines = [head]
        lines += [f"    keeps  {text}" for text in keeps]
        lines += [f"    drops  {text}" for text in drops]
        return "\n".join(lines)
    text = f"output definitions: {head}"
    if keeps:
        text += " | keeps: " + "; ".join(keeps)
    if drops:
        text += " | drops: " + "; ".join(drops)
    return text


def _render_entry(entry):
    inside = ", ".join(
        part for part in (
            f"depth {entry['depth']}" if entry["depth"] is not None else None, entry.get("detail"),
        ) if part
    )
    return f"{entry['tag']}" + (f" ({inside})" if inside else "") + (f": {entry['reason']}" if entry.get("reason") else "")


def _original_input_dataset(dataset, campaign_name):
    """The Input Dataset the stage started with: `dataset` without the
    Recovery Dataset suffix."""
    suffix = RECOVERY_DATASET_SUFFIX.format(campaign_name=campaign_name)
    while dataset.endswith(suffix) and len(dataset) > len(suffix):
        dataset = dataset[:-len(suffix)]
    return dataset


def describe_lineage_tags(cfg, session, sam):
    """Every lineage tag found under the sampled inputs, as [{tag, depth,
    newest_create_date, status}] sorted by tag. `status` is "accepted" (an
    Output Definition of this stage), "test" (a Test Launch tag, ignored) or
    "rejected" (another campaign's). Only reads. Raises
    OutputDefinitionsError when the stage cannot be told apart."""
    trace = trace_output_definitions(cfg, session, sam)
    if trace["scan_error"]:
        raise OutputDefinitionsError(trace["scan_error"])
    return trace["rows"]


class _Steps:
    """The steps of one inference, logged as they are made."""

    def __init__(self):
        self.steps = []

    def add(self, summary, keeps=(), drops=(), name=None, final=False):
        number = len(STEP_NAMES) if final else (
            STEP_NAMES.index(name) + 1 if name else len(self.steps) + 1
        )
        step = {
            "step": number, "name": STEP_NAMES[number - 1],
            "summary": summary, "keeps": list(keeps), "drops": list(drops),
        }
        self.steps.append(step)
        logging.info("%s", render_step(step))
        return step


def _entry(tag, depth, detail=None, reason=None):
    return {"tag": tag, "depth": depth, "detail": detail, "reason": reason}


def _trace(cfg, session, sam, outcome):
    """Runs the inference and returns {steps, rows, definitions, error,
    scan_error}; never raises for a failed inference."""
    steps = _Steps()
    trace = {"steps": [], "rows": [], "definitions": [], "error": None, "scan_error": None}

    def finish(error=None, definitions=(), keeps=()):
        if error:
            steps.add(f"nothing recorded: {error}", final=True)
        else:
            steps.add(f"{len(definitions)} definition(s) {outcome}", keeps, final=True)
        trace["steps"] = steps.steps
        trace["error"] = error
        trace["definitions"] = list(definitions)
        return trace

    def stop_scan(error):
        trace["scan_error"] = error
        return finish(error)

    source = _launch_source(session)
    if not source:
        steps.add("no Submission with a launch command")
        return stop_scan("no Submission with a launch command -- cannot tell this stage's outputs apart")
    launch = _parse_launch_command(source["command"])
    patterns, readable = _postscript_patterns(launch)

    if source["submission_id"] is None:
        origin = "Submission unknown"
    elif source["is_test"]:
        origin = f"Submission {source['submission_id']} (newest of any kind, a Test Launch: no other had a command)"
    else:
        origin = f"Submission {source['submission_id']} (not a Test Launch)"
    if source.get("windows"):
        origin += f"; {source['windows']} POMS window(s) read"
    postscript = (
        "no postscript named" if not launch["postscript"]
        else f"postscript {launch['postscript']} ({'readable' if readable else 'not readable'})"
    )
    version = f"{launch['version']} (from {launch['version_source']})" if launch["version"] else None
    steps.add(
        f"{origin}; production name {launch['production_name']}; sample {launch['sample']}; "
        f"version {version}; {postscript}"
    )

    stage_dataset = session.get_stage_params()["dataset"]
    input_dataset = _original_input_dataset(stage_dataset, cfg["campaign_name"])
    # Lineage is read under the launch's own window, where most inputs
    # already have children; verification still reaches the whole dataset.
    scan_dataset = launch["dataset"] or input_dataset
    lineage, newest, stats = _lineage_tags(sam, scan_dataset)
    input_names = (
        stats["input_names"] if scan_dataset == input_dataset else set(sam.listFiles(defname=input_dataset))
    )
    dataset_text = (
        f"original Input Dataset {input_dataset} (stage's dataset {stage_dataset}, recovery suffix stripped)"
        if input_dataset != stage_dataset else f"Input Dataset {input_dataset}"
    )
    scan_text = f"launch dataset {scan_dataset}: " if scan_dataset != input_dataset else ""
    steps.add(
        f"{dataset_text}; {scan_text}{stats['probed']} of {stats['inputs']} input(s) probed, "
        f"{stats['with_children']} with children"
    )

    ordered = sorted(lineage)
    steps.add(
        f"{len(ordered)} tag(s) found",
        [_entry(t, lineage[t], f"{len(stats['files'][t])} file(s)") for t in ordered],
    )

    test_tags = [t for t in ordered if TEST_TAG_PATTERN.match(t)]
    real = [t for t in lineage if t not in test_tags]
    steps.add(
        f"{len(test_tags)} Test Launch tag(s) dropped (test_, test<N>_)",
        [_entry(t, lineage[t]) for t in real],
        [_entry(t, lineage[t], reason="Test Launch tag") for t in test_tags],
    )

    identities = _identities(launch)
    unexpected = []
    verified = {}
    if patterns:
        steps.add(f"{len(patterns)} pattern(s): {', '.join(patterns)}")
        candidates = _candidates(sam, patterns, real)
        steps.add(
            f"{len(candidates)} candidate name(s): literal patterns, SAM definitions matching a wildcard "
            "pattern, lineage tags matching a pattern",
            [_entry(t, None, source) for t, source in candidates.items()],
            [_entry(t, lineage[t], reason="Test Launch tag") for t in test_tags if _matches(t, patterns)],
            name="candidates",
        )
        verified, failed = _verify(sam, input_names, candidates, launch)
        steps.add(
            f"{len(verified)} of {len(candidates)} candidate(s) are outputs of this stage "
            f"(files in SAM, first parents reach {input_dataset}, metadata agrees)",
            [_entry(t, depth, candidates[t]) for t, depth in verified.items()],
            [_entry(t, None, reason=reason) for t, reason in failed.items()],
            name="verification",
        )
        names = list(verified)
        unexpected = [
            t for t in real
            if not _matches(t, patterns) and _carries(t, identities, launch["version"])
        ]
        drops = []
        for t in real:
            if t in names:
                continue
            if t in unexpected:
                reason = "disagreement: carries the production name and version but matches no pattern"
            elif t in failed:
                reason = f"matches a pattern but failed verification: {failed[t]}"
            else:
                reason = "another campaign's: matches no pattern"
            drops.append(_entry(t, lineage[t], reason=reason))
        steps.add(
            "verified names are this stage's; other lineage tags are cross-checked",
            [_entry(t, verified[t]) for t in names], drops, name="classification",
        )
    else:
        why = "not readable" if launch["postscript"] and not readable else "no pattern in it"
        steps.add(f"no patterns: postscript {why}")
        # The postscript is gone, so is the knowledge of whether it wrote
        # the test_ prefix into the names: ignore it.
        identities = _identities(launch, ignore_test_prefix=True)
        if not (identities and launch["version"]):
            steps.add("fallback impossible: no production name or sample, and version, to scope lineage by")
            return stop_scan("launch command has no production name or sample, and version, to scope lineage by")
        scopes = [f"{identity}_{launch['version']}" for identity in identities]
        scope = " or ".join(scopes)
        names = [t for t in real if any(f"_{s}_" in f"_{t}_" for s in scopes)]
        logging.warning(
            "output definitions: postscript %s not readable -- fallback to lineage names carrying %s",
            launch["postscript"], scope,
        )
        steps.add(
            f"fallback scope {scope}: tags carrying one are this stage's",
            [_entry(t, lineage[t]) for t in names],
            [_entry(t, lineage[t], reason=f"does not carry {scope}") for t in real if t not in names],
            name="classification",
        )

    status = {t: "test" for t in test_tags}
    status.update({t: "accepted" for t in names})
    trace["rows"] = [
        {
            "tag": t, "depth": verified.get(t, lineage.get(t)), "newest_create_date": newest.get(t),
            "status": status.get(t, "rejected"),
        }
        for t in sorted(set(lineage) | set(names))
    ]

    if unexpected:
        logging.error(
            "output definitions: postscript and lineage disagree -- postscript patterns=%s lineage names=%s",
            patterns, names + unexpected,
        )
        return finish(f"lineage names match no postscript pattern: {unexpected}")
    if not names:
        return finish("no output definition found by postscript or lineage")
    depths = {t: verified.get(t, lineage.get(t)) for t in names}
    definitions = [{"name": t, "depth": depths[t]} for t in names]
    return finish(definitions=definitions, keeps=[_entry(t, depths[t]) for t in names])


def _launch_source(session):
    """{command, submission_id, is_test} from the session, or None. A
    session without get_launch_source() gives the command alone."""
    reader = getattr(session, "get_launch_source", None)
    if reader:
        source = reader()
    else:
        command = session.get_launch_command()
        source = {"command": command, "submission_id": None, "is_test": False}
    return source if source and source["command"] else None


def _parse_launch_command(command):
    variables = dict(MT_EXPORT_PATTERN.findall(command))
    postscript = _postscript_path(command)
    version, source = None, None
    for found, label in (
        (VERSION_PATTERN.search(command), "--mdappversion"),
        (re.search(rf"(?<![\w-]){PROJECT_VERSION_VARIABLE}=(\S+)", command), PROJECT_VERSION_VARIABLE),
        (SETUP_VERSION_PATTERN.search(command), "the sbndcode/icaruscode setup"),
    ):
        if found:
            version, source = found.group(1), label
            break
    if version:
        variables[VERSION_VARIABLE] = version
    return {
        "variables": variables,
        "version": version,
        "version_source": source,
        "sample": variables.get(SAMPLE_VARIABLE),
        "production_name": variables.get(PRODUCTION_NAME_VARIABLE),
        "postscript": postscript,
        "dataset": (LAUNCH_DATASET_PATTERN.search(command) or [None, None])[1],
    }


def _postscript_path(command):
    """The shipped postscript the job runs; the first shipped one when no
    postscript line names a shipped file. A submit.f_N or postscript
    changed through param_overrides is already in the command."""
    shipped = POSTSCRIPT_PATH_PATTERN.findall(command)
    for name in reversed(POSTSCRIPT_RUN_PATTERN.findall(command)):
        for path in shipped:
            if path.rsplit("/", 1)[-1] == name:
                return path
    return shipped[0] if shipped else None


def _postscript_patterns(launch):
    """(patterns, readable): expected-name globs from the postscript's
    *DTAG* assignments, and whether the file could be read. The script is
    read, never executed."""
    if not launch["postscript"]:
        return [], False
    try:
        with open(launch["postscript"]) as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return [], False

    assigned = {}
    building_blocks = set()

    def substitute(match):
        name = match.group(1) or match.group(2)
        if name in assigned:
            building_blocks.add(name)
            return assigned[name]
        return launch["variables"].get(name, "*")

    for name, value in DTAG_ASSIGNMENT_PATTERN.findall(text):
        assigned[name] = SHELL_VARIABLE_PATTERN.sub(substitute, value.strip("\"'"))
    patterns = [
        glob for name, glob in assigned.items()
        if name not in building_blocks and glob.strip("*")
    ]
    return patterns, True


def _matches(tag, patterns):
    return any(fnmatchcase(tag, pattern) for pattern in patterns)


def _identities(launch, ignore_test_prefix=False):
    """The names a stage's tags carry besides the version: the production
    name and the sample. With `ignore_test_prefix` a leading `test_` is
    dropped: a postscript that is gone cannot say whether it wrote it into
    the names."""
    found = []
    for value in (launch["production_name"], launch["sample"]):
        value = value or ""
        if ignore_test_prefix and value.startswith(TEST_PREFIX):
            value = value[len(TEST_PREFIX):]
        if value and value not in found:
            found.append(value)
    return found


def _carries(tag, identities, version):
    """Whether a Dataset.Tag carries one of `identities` and the version,
    each as whole `_`-delimited tokens, anywhere in the tag."""
    if not (identities and version):
        return False
    padded = f"_{tag}_"
    return f"_{version}_" in padded and any(f"_{i}_" in padded for i in identities)


def _lineage_tags(sam, input_dataset):
    """({Dataset.Tag: depth below the input}, {Dataset.Tag: newest
    create_date seen}, stats) over the descendants of the Input Dataset's
    files, or of a spread sample of them when it is large. `stats` is {inputs,
    probed, with_children, files: {tag: set of file names}}."""
    all_inputs = list(sam.listFiles(defname=input_dataset))
    read_all = len(all_inputs) <= LINEAGE_MAX_PROBED_INPUTS
    inputs = all_inputs if read_all else all_inputs[::len(all_inputs) // LINEAGE_MAX_PROBED_INPUTS]
    tags = {}
    newest = {}
    files = {}
    sampled = 0
    probed = 0
    for probed, input_file in enumerate(inputs, start=1):
        level = _children(sam, input_file)
        if level:
            sampled += 1
            _walk_descendants(sam, level, tags, newest, files)
        if not read_all and (sampled >= LINEAGE_SAMPLE_SIZE or probed >= LINEAGE_MAX_PROBED_INPUTS):
            break
    return tags, newest, {
        "inputs": len(all_inputs), "probed": probed, "with_children": sampled, "files": files,
        "input_names": set(all_inputs),
    }


def _walk_descendants(sam, level, tags, newest, files):
    seen = set()
    depth = 1
    while level and depth <= LINEAGE_MAX_DEPTH:
        seen.update(level)
        for metadata in sam.getMultipleMetadata(level):
            tag = metadata.get("Dataset.Tag")
            if tag:
                tags[tag] = min(depth, tags.get(tag, depth))
                files.setdefault(tag, set()).add(metadata.get("file_name"))
                created = metadata.get("create_date")
                if created and created > newest.get(tag, ""):
                    newest[tag] = created
        level = list(dict.fromkeys(
            child for name in level for child in _children(sam, name) if child not in seen
        ))
        depth += 1


def _children(sam, file_name):
    """The children of `file_name`; none when SAM does not have the file
    (it is retired or unknown)."""
    try:
        return [child["file_name"] for child in sam.getFileLineage("children", file_name)]
    except Exception as e:
        if not sam_client.is_file_not_found(e):
            raise
        return []


def _candidates(sam, patterns, lineage_tags):
    """{name: source} of the names the postscript patterns can mean: a
    pattern with no wildcard is a name; a wildcard one gives the SAM
    definitions it matches; the lineage tags matching a pattern are
    added. Test Launch names are left out."""
    found = {}
    for pattern in patterns:
        if not any(c in pattern for c in "*?["):
            found.setdefault(pattern, "named by the postscript")
            continue
        for name in sam.listDefinitions(defname=pattern.replace("*", "%")):
            if fnmatchcase(name, pattern):
                found.setdefault(name, "SAM definition")
    for tag in lineage_tags:
        if _matches(tag, patterns):
            found.setdefault(tag, "lineage")
    return {t: source for t, source in sorted(found.items()) if not TEST_TAG_PATTERN.match(t)}


def _verify(sam, input_names, candidates, launch):
    """({name: depth}, {name: reason}): the candidates that are outputs of
    this stage, and why each other one is not."""
    verified, failed = {}, {}
    for name in candidates:
        dimension = f"dataset.tag {name}"
        if sam.countFiles(dimensions=dimension) <= 0:
            failed[name] = "no files in SAM"
            continue
        files = _sample_files(sam, name)
        mismatch = _metadata_mismatch(sam, files, launch)
        if mismatch:
            failed[name] = f"metadata mismatch: {mismatch}"
            continue
        depths = _depths_to_input(sam, input_names, files)
        missing = sum(1 for d in depths.values() if d is None)
        if not files or missing:
            failed[name] = (
                f"{missing} of {len(files)} sampled file(s) are not descendants of this Input Dataset"
            )
            continue
        verified[name] = min(depths.values())
    return dict(sorted(verified.items(), key=lambda item: (item[1], item[0]))), failed


def _metadata_mismatch(sam, files, launch):
    for metadata in sam.getMultipleMetadata(files):
        for field, key in METADATA_FIELDS:
            found, expected = metadata.get(field), launch.get(key)
            if found and expected and found != expected:
                return f"{field} {found} != {expected} from the launch command"
    return None


def _sample_files(sam, name):
    """Up to VERIFY_SAMPLE_FILES files tagged `name`: through the definition
    of that name when SAM has one (fast), else by Dataset.Tag (slow)."""
    try:
        return list(sam.listFiles(dimensions=f"defname: {name} with limit {VERIFY_SAMPLE_FILES}"))
    except Exception as e:
        if "Definition" not in type(e).__name__ and "Dimension" not in type(e).__name__:
            raise
    return list(sam.listFiles(dimensions=f"dataset.tag {name} with limit {VERIFY_SAMPLE_FILES}"))


def _depths_to_input(sam, input_names, files):
    """{file: steps up the first parents to a file of the Input Dataset, or
    None when none is reached within LINEAGE_MAX_DEPTH}."""
    depths = dict.fromkeys(files)
    current = {f: f for f in files}
    for depth in range(1, LINEAGE_MAX_DEPTH + 1):
        parents = {f: _first_parent(sam, name) for f, name in current.items()}
        parents = {f: p for f, p in parents.items() if p}
        if not parents:
            break
        members = {p for p in parents.values() if p in input_names}
        for f, parent in parents.items():
            if parent in members:
                depths[f] = depth
        current = {f: p for f, p in parents.items() if p not in members}
    return depths


def _first_parent(sam, file_name):
    """The first parent of `file_name`; none when SAM has no such file."""
    try:
        parents = sam.getFileLineage("parents", file_name)
    except Exception as e:
        if not sam_client.is_file_not_found(e):
            raise
        return None
    return parents[0]["file_name"] if parents else None
