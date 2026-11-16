#!/bin/bash
# List input files that never became a parent of any file in an output
# definition, i.e. inputs that produced no output.
#
#   missing_inputs.sh "<input dimension query>" <output_definition>
#   e.g. missing_inputs.sh "defname:run3_raw_data_ongoing and create_date >= '2026-10-28T09:00:00' and create_date < '2026-10-28T12:00:00'" \
#          data_SBND2026A_..._reco1_sbnd
#
# Uses isparentof (inputs are the PARENTS of outputs). ischildof would return
# almost every input. Mints the production token via poms_auto_submit setup.sh
# (samweb fails with RecursionError without a token). No strict mode (UPS).

# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
POMS_AUTO_SUBMIT_DIR=$(cd "$HERE/../../../.." && pwd -P)
[[ $# -eq 2 ]] || { echo "Usage: $0 \"<input query>\" <output_definition>" >&2; exit 1; }
query=$1; outdef=$2
cd "$POMS_AUTO_SUBMIT_DIR" || exit 1
source setup.sh >/tmp/sbnd_poms_setup_$$.log 2>&1 \
  || { echo "setup.sh failed, see /tmp/sbnd_poms_setup_$$.log" >&2; exit 1; }
echo "inputs total: $(samweb count-files "$query" 2>&1 | tail -1)" >&2
samweb list-files "($query) minus isparentof:(defname:$outdef)" 2>&1 | grep -v -i tracing
