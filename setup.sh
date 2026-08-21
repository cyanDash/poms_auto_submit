# Source this script to set up everything needed to run poms_auto_submit.py and
# the test/ scripts:
#
#   source setup.sh [--role <role>]
#
# Sets up UPS + poms_client (so POMS_CLIENT_DIR points at the CVMFS-installed
# poms_client.py/client.cfg, and `requests` comes from poms_client's own
# required UPS dependency, python_request), then creates (first run only) and
# activates a local python venv, syncing it against requirements.txt on every
# source (not just first creation).
#
# --role <role>: passed through as htgettoken's -r flag when fetching the
# bearer token (e.g. --role production). Omit it to fetch a token without a
# role, same as running htgettoken with no -r.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "setup.sh must be sourced, not executed: 'source setup.sh'" >&2
    return 1 2>/dev/null || exit 1
fi

_poms_auto_submit_role=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --role)
            _poms_auto_submit_role="$2"
            shift 2
            ;;
        *)
            echo "unknown argument: $1 (usage: source setup.sh [--role <role>])" >&2
            return 1 2>/dev/null || exit 1
            ;;
    esac
done

_poms_auto_submit_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh
setup poms_client

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
[[ -z "$BEARER_TOKEN_FILE" ]] && export BEARER_TOKEN_FILE=/tmp/bt_u$(id -u)
if [[ -n "$_poms_auto_submit_role" ]]; then
    htgettoken -v -a htvaultprod.fnal.gov -i sbnd -r "$_poms_auto_submit_role"
else
    htgettoken -v -a htvaultprod.fnal.gov -i sbnd
fi

# Refresh POMS's own copy of the vault token (separate from the local
# vt_/bt_ files above; this is what check_auth's "looks stale" warning
# checks). No --refresh: its staleness pre-check errors out on the
# bearer-token path and always re-uploads anyway, just noisier.
export WEB_CONFIG="${WEB_CONFIG:-/dev/null}"  # upload_file requires this set, even unused here
$POMS_CLIENT_DIR/bin/upload_file --vaulttoken --experiment sbnd --poms_role "${_poms_auto_submit_role:-analysis}"

unset _poms_auto_submit_role
