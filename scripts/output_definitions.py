"""output_definitions: infers a Campaign Stage's Output Definitions and is
the only writer of the State File's `output_definitions` section.
get_output_definitions() is the module's main interface; it raises
OutputDefinitionsError. describe_lineage_tags() is the read-only view of the
tags it chooses from. See docs/adr/0023.
"""

import logging
import re
from fnmatch import fnmatchcase

import sam_client
import state

SECTION = "output_definitions"

# Fixed constants, not config knobs. An Input Dataset of at most
# LINEAGE_MAX_PROBED_INPUTS files is read in full; a larger one is probed
# until LINEAGE_SAMPLE_SIZE inputs with children were read, or
# LINEAGE_MAX_PROBED_INPUTS inputs were probed.
LINEAGE_SAMPLE_SIZE = 20
LINEAGE_MAX_PROBED_INPUTS = 200
LINEAGE_MAX_DEPTH = 6

# scripts/run_recovery.sh names the Recovery Dataset
# <input>_<campaign_name>_recovery_campaign.
RECOVERY_DATASET_SUFFIX = "_{campaign_name}_recovery_campaign"

POSTSCRIPT_PATH_PATTERN = re.compile(r"-f\s+dropbox://(\S*metadata_postscripts[^/\s]*\.sh)")
MT_EXPORT_PATTERN = re.compile(r"export\s+(MT_\w+)=(\S+)")
VERSION_PATTERN = re.compile(r"--mdappversion[\s=]+(\S+)")
# The postscripts name the software version this, and never assign it.
VERSION_VARIABLE = "SBNDCODE_VERSION"
PRODUCTION_NAME_VARIABLE = "MT_PRODUCTIONNAME"
TEST_PREFIX = "test_"
# A Test Launch writes outputs under the same inputs with tags like these.
TEST_TAG_PATTERN = re.compile(r"^test\d*_")

DTAG_ASSIGNMENT_PATTERN = re.compile(r"^[ \t]*(?:export[ \t]+)?(\w*DTAG\w*)=(\S*)", re.MULTILINE)
SHELL_VARIABLE_PATTERN = re.compile(r"\$\{(\w+)\}|\$(\w+)")


class OutputDefinitionsError(RuntimeError):
    """The output definitions could not be inferred; nothing was recorded."""


def get_output_definitions(cfg, session, sam, dry_run=False):
    """The Campaign Stage's Output Definitions as [{name, depth}]: what the
    State File holds, else inferred and recorded. A flat list of names is
    never trusted: it is inferred again and replaced. A dry run records
    nothing. Raises OutputDefinitionsError when the
    definitions cannot be inferred; the State File is then left as it is."""
    recorded = state.read(cfg, SECTION, []) or []
    if recorded and all(isinstance(entry, dict) for entry in recorded):
        return [dict(entry) for entry in recorded]

    definitions = _infer(cfg, session, sam)
    if not dry_run:
        state.write(cfg, SECTION, definitions)
    return definitions


def _original_input_dataset(dataset, campaign_name):
    """The Input Dataset the stage started with: `dataset` without the
    Recovery Dataset suffix."""
    suffix = RECOVERY_DATASET_SUFFIX.format(campaign_name=campaign_name)
    while dataset.endswith(suffix) and len(dataset) > len(suffix):
        dataset = dataset[:-len(suffix)]
    return dataset


def _infer(cfg, session, sam):
    scan = _scan(cfg, session, sam)
    if scan["unexpected"]:
        logging.error(
            "output definitions: postscript and lineage disagree -- postscript patterns=%s lineage names=%s",
            scan["patterns"], scan["names"] + scan["unexpected"],
        )
        raise OutputDefinitionsError(f"lineage names match no postscript pattern: {scan['unexpected']}")
    if not scan["names"]:
        raise OutputDefinitionsError("no output definition found by postscript or lineage")
    definitions = [{"name": name, "depth": scan["lineage"][name]} for name in scan["names"]]
    logging.info("output definitions: %s", definitions)
    return definitions


def describe_lineage_tags(cfg, session, sam):
    """Every lineage tag found under the sampled inputs, as [{tag, depth,
    newest_create_date, status}] sorted by tag. `status` is "accepted" (an
    Output Definition of this stage), "test" (a Test Launch tag, ignored) or
    "rejected" (another campaign's). Only reads. Raises
    OutputDefinitionsError when the stage cannot be told apart."""
    scan = _scan(cfg, session, sam)
    rows = []
    for tag in sorted(scan["lineage"]):
        status = "test" if tag in scan["test_tags"] else "accepted" if tag in scan["names"] else "rejected"
        rows.append({
            "tag": tag, "depth": scan["lineage"][tag],
            "newest_create_date": scan["newest"].get(tag), "status": status,
        })
    return rows


def _scan(cfg, session, sam):
    """The lineage tags of the Input Dataset sorted out: {lineage, newest,
    test_tags, names, unexpected, patterns}. `lineage` holds every tag,
    Test Launch ones included; `unexpected` are tags the postscript patterns
    reject that still carry the production name and version."""
    command = session.get_launch_command()
    if not command:
        raise OutputDefinitionsError("no Submission with a launch command -- cannot tell this stage's outputs apart")
    launch = _parse_launch_command(command)

    input_dataset = _original_input_dataset(session.get_stage_params()["dataset"], cfg["campaign_name"])
    lineage, newest = _lineage_tags(sam, input_dataset)
    test_tags = sorted(tag for tag in lineage if TEST_TAG_PATTERN.match(tag))
    if test_tags:
        logging.info("output definitions: ignoring %d Test Launch tag(s): %s", len(test_tags), test_tags)
    real = [tag for tag in lineage if tag not in test_tags]
    patterns = _postscript_patterns(launch)

    unexpected = []
    if patterns:
        names = [tag for tag in real if _matches(tag, patterns)]
        unexpected = [
            tag for tag in real
            if tag not in names and _carries(tag, launch["production_name"], launch["version"])
        ]
    else:
        # The postscript is gone, so is the knowledge of whether it wrote
        # the test_ prefix into the names: ignore it.
        production_name = launch["production_name"] or ""
        if production_name.startswith(TEST_PREFIX):
            production_name = production_name[len(TEST_PREFIX):]
        if not (production_name and launch["version"]):
            raise OutputDefinitionsError("launch command has no production name and version to scope lineage by")
        scope = f"{production_name}_{launch['version']}"
        names = [tag for tag in real if f"_{scope}_" in f"_{tag}_"]
        logging.warning(
            "output definitions: postscript %s not readable -- fallback to lineage names carrying %s",
            launch["postscript"], scope,
        )
    return {
        "lineage": lineage, "newest": newest, "test_tags": test_tags,
        "names": names, "unexpected": unexpected, "patterns": patterns,
    }


def _parse_launch_command(command):
    variables = dict(MT_EXPORT_PATTERN.findall(command))
    version = VERSION_PATTERN.search(command)
    postscript = POSTSCRIPT_PATH_PATTERN.search(command)
    if version:
        variables[VERSION_VARIABLE] = version.group(1)
    return {
        "variables": variables,
        "version": version.group(1) if version else None,
        "production_name": variables.get(PRODUCTION_NAME_VARIABLE),
        "postscript": postscript.group(1) if postscript else None,
    }


def _postscript_patterns(launch):
    """Expected-name globs from the postscript's *DTAG* assignments, [] when
    the file cannot be read. The script is read, never executed."""
    if not launch["postscript"]:
        return []
    try:
        with open(launch["postscript"]) as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return []

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
    return [
        glob for name, glob in assigned.items()
        if name not in building_blocks and glob.strip("*")
    ]


def _matches(tag, patterns):
    return any(fnmatchcase(tag, pattern) for pattern in patterns)


def _carries(tag, production_name, version):
    """Whether a Dataset.Tag carries a production name and a version."""
    if not (production_name and version):
        return False
    padded = f"_{tag}_"
    return f"_{production_name}_" in padded and f"_{version}_" in padded


def _lineage_tags(sam, input_dataset):
    """({Dataset.Tag: depth below the input}, {Dataset.Tag: newest
    create_date seen}) over the descendants of the Input Dataset's files, or
    of a sample of them when it is large."""
    inputs = list(sam.listFiles(defname=input_dataset))
    read_all = len(inputs) <= LINEAGE_MAX_PROBED_INPUTS
    tags = {}
    newest = {}
    sampled = 0
    for probed, input_file in enumerate(inputs, start=1):
        level = _children(sam, input_file)
        if level:
            sampled += 1
            _walk_descendants(sam, level, tags, newest)
        if not read_all and (sampled >= LINEAGE_SAMPLE_SIZE or probed >= LINEAGE_MAX_PROBED_INPUTS):
            break
    return tags, newest


def _walk_descendants(sam, level, tags, newest):
    seen = set()
    depth = 1
    while level and depth <= LINEAGE_MAX_DEPTH:
        seen.update(level)
        for metadata in sam.getMultipleMetadata(level):
            tag = metadata.get("Dataset.Tag")
            if tag:
                tags[tag] = min(depth, tags.get(tag, depth))
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
