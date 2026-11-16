#!/bin/bash
# Wrapper around poms_auto_submit.py: sources its own setup.sh (UPS poms_client
# + sam_web_client, venv, sbndpro managed-token htgettoken -- confirmed to work
# run as a regular user, not just sbndpro itself) then runs it against a given
# config. Always shows a --dry-run first; only actually submits with --real.
#
# Usage:
#   submit.sh <config.ini>           # dry-run only
#   submit.sh <config.ini> --real    # dry-run, then submit for real
#   submit.sh <config.ini> --real --follow-launch   # also print POMS's launch stdout while waiting for the jobsub id
#
# See poms_auto_submit/README.md and configs/config.ini for the ini format:
#   [poms] experiment/campaign_name/campaign_stage_name
#   [decision] pct_complete_threshold/submit_two_slices/max_splits/
#              test_launch/do_recovery/do_cleanup
# switch, last_split and recovery_handled are not config keys: the script
# keeps them in logs/<campaign_name>/state_<ini name>.json.
#
# test_launch=1 routes the submission through the campaign stage's
# test_param_overrides instead of its real param_overrides -- use it for any
# throwaway/validation submission. For a single manual submission (not a
# cron/campaign run), set max_splits=1 and leave out do_recovery/do_cleanup
# (both off when missing) so the script's recovery/cleanup machinery stays
# out of the way. Reusing the same ini after a real submit is skipped as
# "max_splits reached" until "last_split" is set back to 0 in its state file
# (or the state file is deleted).
#
# NOTE: no strict mode (`set -e`/`-u`/pipefail) until AFTER setup.sh is
# sourced. poms_auto_submit's setup.sh sources UPS's setups.sh, which both
# references unset variables (e.g. UPS_OVERRIDE) and has internal commands
# that return nonzero as part of its own normal control flow -- either one
# silently kills the whole script (no error message, looks like a hang) if
# strict mode is already active while it runs. Confirmed via `bash -x` trace
# this session on fetch_and_analyze.sh. See REFERENCE.md.

# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
POMS_AUTO_SUBMIT_DIR=$(cd "$HERE/../../../.." && pwd -P)

usage() { echo "Usage: $0 <config.ini> [--real [--follow-launch]]" >&2; exit 1; }
[[ $# -ge 1 ]] || usage
config=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
real=${2:-}
follow=${3:-}

[[ -f "$config" ]] || { echo "Config not found: $config" >&2; exit 1; }
if [[ -n "$real" && "$real" != "--real" ]]; then usage; fi
if [[ -n "$follow" && "$follow" != "--follow-launch" ]]; then usage; fi

cd "$POMS_AUTO_SUBMIT_DIR" || exit 1
# shellcheck disable=SC1091
source setup.sh >/tmp/sbnd_poms_setup_$$.log 2>&1
setup_rc=$?
if [[ $setup_rc -ne 0 ]]; then
  echo "setup.sh failed (exit $setup_rc) -- see /tmp/sbnd_poms_setup_$$.log" >&2
  exit 1
fi

# All UPS sourcing is done -- safe to turn on strict mode now.
set -eo pipefail

echo "== Dry run against ${config} =="
./scripts/poms_auto_submit.py --config "$config" --dry-run

if [[ "$real" == "--real" ]]; then
  echo
  echo "== Submitting for real =="
  ./scripts/poms_auto_submit.py --config "$config" ${follow:+"$follow"}
else
  echo
  echo "(dry run only -- pass --real to actually submit)"
fi
