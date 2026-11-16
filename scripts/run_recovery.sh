#!/usr/bin/env bash
#
# Automates TODO.md item 1 (steps 1-6). Called by scripts/recovery.py; see
# docs/adr/0010, 0011.
#
# Usage: run_recovery.sh <input_dataset> <campaign_name> <prestage_log> <output_defname>...
#
# The output definitions are inferred by scripts/output_definitions.py and
# passed in; this script does not discover them.
#
# stdout: ratio (or N/A), threshold, then dataset name or NO_RECOVERY_NEEDED
# -- one per line. Anything else: stderr message, exit != 0.

set -uo pipefail

# Fixed engineering constants, not per-campaign policy knobs.
RATIO_THRESHOLD="0.98"          # TODO.md step 5
DATA_CAMPAIGN_MARKER="data_EventBuilder"  # TODO.md step 6
PRESTAGE_PARALLEL="5"           # TODO.md step 6, matches setup_recovery.sh

if [[ $# -lt 4 ]]; then
    echo "usage: $(basename "$0") <input_dataset> <campaign_name> <prestage_log> <output_defname>..." >&2
    exit 1
fi

INPUT_DATASET=$1
CAMPAIGN_NAME=$2
PRESTAGE_LOG=$3
shift 3
outputs=("$@")

RECOVERY_NAME="${INPUT_DATASET}_${CAMPAIGN_NAME}_recovery_campaign"

# --- campaign type, from the Input Dataset's first file name ---
if ! input_files=$(samweb list-definition-files "$INPUT_DATASET"); then
    echo "samweb list-definition-files $INPUT_DATASET failed" >&2
    exit 2
fi
if [[ -z "$input_files" ]]; then
    echo "input dataset $INPUT_DATASET has no files" >&2
    exit 2
fi

first_file=${input_files%%$'\n'*}
campaign_type="mc"
[[ "$first_file" == *"$DATA_CAMPAIGN_MARKER"* ]] && campaign_type="data"

# --- build the "missing at least one output" dimension and create the
# recovery dataset: not (isparentof out1 and isparentof out2 ...). One
# isparentof per output -- a file belongs to only one output definition, so
# a single isparentof over "defname: out1 and defname: out2" is an empty set
# and "not" of it would keep every input file. ---
outputs_clause=""
for o in "${outputs[@]}"; do
    if [[ -z "$outputs_clause" ]]; then
        outputs_clause="isparentof: (defname: $o)"
    else
        outputs_clause="$outputs_clause and isparentof: (defname: $o)"
    fi
done
dimension="defname: $INPUT_DATASET and not ($outputs_clause)"

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
if [[ "$recovery_count" -eq 0 ]] || awk "BEGIN { exit !($output_count / $input_count > $RATIO_THRESHOLD) }"; then
    echo "$ratio"
    echo "$RATIO_THRESHOLD"
    echo "NO_RECOVERY_NEEDED"
    exit 0
fi

# --- step 6: prestage a data campaign's recovery dataset, detached ---
# Must not block poms_auto_submit.py's per-campaign-stage lock (ADR-0006)
# for the hours prestaging can take; setsid+disown detaches it.
if [[ "$campaign_type" == "data" ]]; then
    setsid samweb prestage-dataset --parallel="$PRESTAGE_PARALLEL" --defname="$RECOVERY_NAME" \
        </dev/null >>"$PRESTAGE_LOG" 2>&1 &
    disown
fi

echo "$ratio"
echo "$RATIO_THRESHOLD"
echo "$RECOVERY_NAME"
