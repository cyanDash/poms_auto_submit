#!/bin/bash
# Sources poms_auto_submit's setup.sh, then runs update_stage_params.py.
# Usage: update_stage_params.sh <campaign_name> <campaign_stage_name> KEY=VALUE [KEY=VALUE ...] [--force]
#
# NOTE: no strict mode until AFTER setup.sh is sourced -- see submit.sh /
# REFERENCE.md for why.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
POMS_AUTO_SUBMIT_DIR=$(cd "$HERE/../../../.." && pwd -P)

cd "$POMS_AUTO_SUBMIT_DIR" || exit 1
# shellcheck disable=SC1091
source setup.sh >/tmp/sbnd_poms_setup_$$.log 2>&1
setup_rc=$?
if [[ $setup_rc -ne 0 ]]; then
  echo "setup.sh failed (exit $setup_rc) -- see /tmp/sbnd_poms_setup_$$.log" >&2
  exit 1
fi

set -eo pipefail
python3 "$HERE/update_stage_params.py" "$@"
