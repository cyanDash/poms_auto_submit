#!/usr/bin/env bash
#
# Automates TODO.md item 1 (steps 1-6). Called by scripts/recovery.py; see
# docs/adr/0010, 0011.
#
# Usage: run_recovery.sh <input_dataset> <campaign_name> <output_defnames_file>
#
# stdout: ratio (or N/A), threshold, then dataset name or NO_RECOVERY_NEEDED
# -- one per line. Anything else: stderr message, exit != 0.
# output_defnames_file gets the output dataset names (step 3), one per line.

set -uo pipefail

# Fixed engineering constants, not per-campaign policy knobs.
RATIO_THRESHOLD="0.98"          # TODO.md step 5
DATA_CAMPAIGN_MARKER="data_EventBuilder"  # TODO.md step 6
PRESTAGE_PARALLEL="5"           # TODO.md step 6, matches setup_recovery.sh

if [[ $# -ne 3 ]]; then
    echo "usage: $(basename "$0") <input_dataset> <campaign_name> <output_defnames_file>" >&2
    exit 1
fi

INPUT_DATASET=$1
CAMPAIGN_NAME=$2
OUTPUT_DEFNAMES_FILE=$3

RECOVERY_NAME="${INPUT_DATASET}_${CAMPAIGN_NAME}_recovery_campaign"

# --- steps 1-2: first input file that actually has output lineage ---
# Not every file need have children (e.g. it was never run, or failed) --
# scan until one does, rather than trusting the first file in the list.
if ! input_files=$(samweb list-definition-files "$INPUT_DATASET"); then
    echo "samweb list-definition-files $INPUT_DATASET failed" >&2
    exit 2
fi
if [[ -z "$input_files" ]]; then
    echo "input dataset $INPUT_DATASET has no files" >&2
    exit 2
fi

probe_file=""
lineage=""
while IFS= read -r candidate; do
    [[ -z "$candidate" ]] && continue
    candidate_lineage=$(samweb file-lineage children "$candidate") || continue
    if [[ -n "$candidate_lineage" ]]; then
        probe_file="$candidate"
        lineage="$candidate_lineage"
        break
    fi
done <<<"$input_files"

if [[ -z "$probe_file" ]]; then
    echo "no file in $INPUT_DATASET has any output lineage (file-lineage children)" >&2
    exit 3
fi

campaign_type="mc"
[[ "$probe_file" == *"$DATA_CAMPAIGN_MARKER"* ]] && campaign_type="data"

# --- step 3: output dataset definitions (Dataset.Tag off each lineage file), deduplicated ---
declare -A seen
outputs=()
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    tag=$(samweb get-metadata "$f" | grep 'Dataset.Tag' | awk -F': ' '{print $2}')
    if [[ -n "$tag" && -z "${seen[$tag]:-}" ]]; then
        seen[$tag]=1
        outputs+=("$tag")
    fi
done <<<"$lineage"

if [[ ${#outputs[@]} -eq 0 ]]; then
    echo "no Dataset.Tag metadata found on any lineage file of $probe_file" >&2
    exit 4
fi
printf '%s\n' "${outputs[@]}" > "$OUTPUT_DEFNAMES_FILE"

# --- build the "missing at least one output" dimension and create the
# recovery dataset. "and" here, not "or": we want files that did not
# produce ALL of their expected outputs, i.e. not(isparentof out1 and
# isparentof out2 and ...) = not out1 or not out2 or ... ---
outputs_clause=""
for o in "${outputs[@]}"; do
    if [[ -z "$outputs_clause" ]]; then
        outputs_clause="defname: $o"
    else
        outputs_clause="$outputs_clause and defname: $o"
    fi
done
dimension="defname: $INPUT_DATASET and not isparentof: ($outputs_clause)"

if ! samweb create-definition "$RECOVERY_NAME" "$dimension" >&2; then
    echo "samweb create-definition failed for $RECOVERY_NAME" >&2
    exit 5
fi

# --- step 4: file counts ---
if ! input_count=$(samweb count-definition-files "$INPUT_DATASET"); then
    echo "samweb count-definition-files $INPUT_DATASET failed" >&2
    exit 6
fi
if ! recovery_count=$(samweb count-definition-files "$RECOVERY_NAME"); then
    echo "samweb count-definition-files $RECOVERY_NAME failed" >&2
    exit 6
fi

# --- step 5: ratio decision ---
if [[ "$input_count" -eq 0 ]]; then
    echo "N/A"
    echo "$RATIO_THRESHOLD"
    echo "NO_RECOVERY_NEEDED"
    exit 0
fi
output_count=$((input_count - recovery_count))
ratio=$(awk "BEGIN { printf \"%.4f\", $output_count / $input_count }")
if awk "BEGIN { exit !($output_count / $input_count > $RATIO_THRESHOLD) }"; then
    echo "$ratio"
    echo "$RATIO_THRESHOLD"
    echo "NO_RECOVERY_NEEDED"
    exit 0
fi

# --- step 6: prestage a data campaign's recovery dataset, detached ---
# Must not block poms_auto_submit.py's per-campaign-stage lock (ADR-0006)
# for the hours prestaging can take; setsid+disown detaches it.
if [[ "$campaign_type" == "data" ]]; then
    prestage_log="${OUTPUT_DEFNAMES_FILE%.txt}_prestage.log"
    setsid samweb prestage-dataset --parallel="$PRESTAGE_PARALLEL" --defname="$RECOVERY_NAME" \
        </dev/null >>"$prestage_log" 2>&1 &
    disown
fi

echo "$ratio"
echo "$RATIO_THRESHOLD"
echo "$RECOVERY_NAME"
