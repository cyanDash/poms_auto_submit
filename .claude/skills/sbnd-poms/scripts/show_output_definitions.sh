#!/bin/bash
# Sources poms_auto_submit's setup.sh, then runs its read-only
# scripts/show_output_definitions.py (writes nothing: no state file, no
# SAM or POMS write).
# Usage: show_output_definitions.sh <campaign_name> <campaign_stage_name> [--all-tags]
#
# NOTE: no strict mode until AFTER setup.sh is sourced -- see submit.sh /
# REFERENCE.md for why.
# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
POMS_AUTO_SUBMIT_DIR=$(cd "$HERE/../../../.." && pwd -P)

if [[ $# -lt 2 ]]; then
  echo "usage: $(basename "$0") <campaign_name> <campaign_stage_name> [--all-tags]" >&2
  exit 1
fi
campaign=$1
stage=$2
shift 2

cd "$POMS_AUTO_SUBMIT_DIR" || exit 1
# shellcheck disable=SC1091
source setup.sh >/tmp/sbnd_poms_setup_$$.log 2>&1
setup_rc=$?
if [[ $setup_rc -ne 0 ]]; then
  echo "setup.sh failed (exit $setup_rc) -- see /tmp/sbnd_poms_setup_$$.log" >&2
  exit 1
fi

set -eo pipefail
python3 scripts/show_output_definitions.py --campaign "$campaign" --stage "$stage" "$@"
