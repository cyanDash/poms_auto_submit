# Source this script to set up everything needed to run poms_slice_cron.py and
# the test/ scripts:
#
#   source setup.sh
#
# Sets up UPS + poms_client (so POMS_CLIENT_DIR points at the CVMFS-installed
# poms_client.py/client.cfg, and `requests` comes from poms_client's own
# required UPS dependency, python_request), then creates (first run only) and
# activates a local python venv, syncing it against requirements.txt on every
# source (not just first creation).

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "setup.sh must be sourced, not executed: 'source setup.sh'" >&2
    return 1 2>/dev/null || exit 1
fi

_poms_slice_cron_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh
setup poms_client

_poms_slice_cron_venv="${_poms_slice_cron_dir}/venv"
if [[ ! -d "${_poms_slice_cron_venv}" ]]; then
    echo "creating venv at ${_poms_slice_cron_venv}"
    python3 -m venv "${_poms_slice_cron_venv}"
fi
source "${_poms_slice_cron_venv}/bin/activate"

_poms_slice_cron_requirements="${_poms_slice_cron_dir}/requirements.txt"
if pip freeze -r "${_poms_slice_cron_requirements}" 2>&1 | grep -q "not installed"; then
    echo "installing/updating requirements from ${_poms_slice_cron_requirements}"
    pip install --upgrade pip >/dev/null
    pip install -r "${_poms_slice_cron_requirements}"
fi

echo "poms_slice_cron environment ready (POMS_CLIENT_DIR=${POMS_CLIENT_DIR})"

unset _poms_slice_cron_dir _poms_slice_cron_venv _poms_slice_cron_requirements

# Get token for poms_client
[[ -z "$BEARER_TOKEN_FILE" ]] && export BEARER_TOKEN_FILE=/tmp/bt_u$(id -u)
htgettoken -v -a htvaultprod.fnal.gov -i sbnd