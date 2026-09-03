# Source this script to set up everything needed to run
# scripts/poms_auto_submit.py and the test/ scripts:
#
#   source setup.sh
#
# Sets up UPS + poms_client (so POMS_CLIENT_DIR points at the CVMFS-installed
# poms_client.py/client.cfg, and `requests` comes from poms_client's own
# required UPS dependency, python_request) and sam_web_client (so `samweb` is
# on $PATH for scripts/run_recovery.sh), then creates (first run only) and
# activates a local python venv, syncing it against requirements.txt on every
# source (not just first creation).
#
# Must be run as the sbndpro user (production members only) — sbndpro has
# managed tokens, so no kinit is needed to fetch a bearer token.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "setup.sh must be sourced, not executed: 'source setup.sh'" >&2
    return 1 2>/dev/null || exit 1
fi

_poms_auto_submit_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh
setup poms_client
setup sam_web_client
export SAM_EXPERIMENT=sbnd

_poms_auto_submit_venv="${_poms_auto_submit_dir}/venv"
if [[ ! -d "${_poms_auto_submit_venv}" ]]; then
    echo "creating venv at ${_poms_auto_submit_venv}"
    python3 -m venv "${_poms_auto_submit_venv}"
fi
source "${_poms_auto_submit_venv}/bin/activate"

_poms_auto_submit_requirements="${_poms_auto_submit_dir}/requirements.txt"
if pip freeze -r "${_poms_auto_submit_requirements}" 2>&1 | grep -q "not installed"; then
    echo "installing/updating requirements from ${_poms_auto_submit_requirements}"
    pip install --upgrade pip >/dev/null
    pip install -r "${_poms_auto_submit_requirements}"
fi

echo "poms_auto_submit environment ready (POMS_CLIENT_DIR=${POMS_CLIENT_DIR})"

unset _poms_auto_submit_dir _poms_auto_submit_venv _poms_auto_submit_requirements

# Get token for poms_client
export HTGETTOKENOPTS="--credkey=sbndpro/managedtokens/fifeutilgpvm01.fnal.gov -r production -a htvaultprod.fnal.gov -i sbnd"
export BEARER_TOKEN_FILE=/tmp/bt_u$(id -u)_poms_auto_submit
htgettoken
