#!/bin/bash
# Fetch (if needed) and analyze a grid job's out/err logs for the recurring
# SBND POMS failure signatures: fife_wrap-level aborts, art/LArSoft
# exceptions (with context), per-stage TrigReport/MemReport, and the SAM
# metadata declare/copy-back check (a job can exit 0 while silently failing
# to declare/copy back any output -- always check that section, not just
# "Final status").
#
# Two modes:
#
#   fetch_and_analyze.sh <jobsub_id> [outdir]
#       Mints a production-role bearer token via poms_auto_submit's sbndpro
#       managed-token credkey, then fetches with jobsub_fetchlog. This works
#       even when your own personal token gets a 401 from jobsub_fetchlog
#       (lacks the /sbnd/production group) -- see REFERENCE.md. Default
#       outdir: . (current directory)
#
#   fetch_and_analyze.sh --offline <outfile> [errfile]
#       Skips the fetch step and analyzes files already on disk (handed to
#       you directly, or found by hand under a DAG job's nested per-node log
#       dir). errfile defaults to swapping .out for .err next to outfile.
#
# jobsub id note: needs the ".<process>" suffix (e.g. "12345.0@jobsub01...")
# -- a bare cluster id makes landscape's fetchlog ownership check fail with a
# misleading 401 even for a job you own. Normalized automatically below.
#
# DAG-job log layout note: for DAG-submitted (dataset.dag) jobs, the actual
# lar-stage log is a fife_wrap*cluster.<clusterid>.<process>.{out,err} pair
# nested under the DAG's own submission-id job dir, alongside
# dagbegin/dagend/sambegin/samend bookkeeping files -- not those bookkeeping
# files themselves. If jobsub_fetchlog only produces those and no .out yet,
# the lar-stage DAG node hasn't produced logs yet (still running, or hasn't
# started) -- wait and refetch rather than treating it as an error.
#
# NOTE: no strict mode (`set -e`/`-u`/pipefail) here at the top. UPS's
# setups.sh (sourced by poms_auto_submit's setup.sh, and again directly below
# for jobsub_client) references unset variables AND has internal commands
# that return nonzero as part of its own normal control flow -- either one
# silently kills the whole script if strict mode is active while it runs.
# Confirmed via `bash -x` trace this session: the script died with zero
# output/error mid-way through setups.sh's internals under `set -eo
# pipefail`, in a way that looked like a hang, not a crash. Strict mode is
# turned on further below, only once all UPS sourcing is done. See
# REFERENCE.md.

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
POMS_AUTO_SUBMIT_DIR=$(cd "$HERE/../../../.." && pwd -P)
DEFAULT_OUTDIR=.

usage() {
  echo "Usage: $0 [--personal-token] [--all] <jobsub_id> [outdir]" >&2
  echo "       $0 --offline <outfile> [errfile]" >&2
  exit 1
}
TOKEN_MODE=production
ANALYZE_ALL=0
while [[ "${1:-}" == --personal-token || "${1:-}" == --all ]]; do
  [[ "$1" == --personal-token ]] && TOKEN_MODE=personal
  [[ "$1" == --all ]] && ANALYZE_ALL=1
  shift
done
[[ $# -ge 1 ]] || usage

grep_tag() {
  local label="$1" file="$2"; shift 2
  [[ -n "$file" && -f "$file" ]] || return 0
  grep -n "$@" "$file" 2>/dev/null | sed -E "s/^([0-9]+)/${label}:\1/"
}
dedupe() { awk '!seen[$0]++'; }

analyze_one() {
  local outfile="$1" errfile="${2:-}"
  [[ -n "$errfile" && -f "$errfile" ]] || errfile=""

  echo
  echo "== $(basename "$outfile") =="
  echo "out: $outfile"
  echo "err: ${errfile:-<none found>}"

  echo
  echo "-- Final status --"
  local block
  block=$( { grep_tag out "$outfile" -E "COMPLETED with exit status|trying to exit"
             grep_tag err "$errfile" -E "COMPLETED with exit status|trying to exit"
           } | dedupe ) || true
  echo "${block:-(not found)}"

  echo
  echo "-- fife_wrap-level failure signatures --"
  block=$( { grep_tag out "$outfile" -E "command not found|No such file or directory|^Running: None|Entering file loop|leaving file loop"
             grep_tag err "$errfile" -E "command not found|No such file or directory|^Running: None|Entering file loop|leaving file loop"
           } | dedupe ) || true
  echo "${block:-(none found)}"

  echo
  echo "-- art/LArSoft exceptions (out + err) --"
  block=$(grep_tag out "$outfile" -E "%MSG-s|ArtException|ProductNotFound|EventProcessorFailure|FatalRootError" -A 4 | dedupe) || true
  echo "${block:-(none found in out)}"

  local exit_line exit_code
  exit_line=$(grep -n "Art has completed and will exit with status" "$outfile" | tail -1 || true)
  exit_code=""
  if [[ -n "$exit_line" ]]; then
    local exit_lineno=${exit_line%%:*}
    exit_code=$(grep -oE "status [0-9]+" <<< "$exit_line" | grep -oE "[0-9]+")

    if [[ -n "$exit_code" && "$exit_code" != "0" ]]; then
      local art_lineno exc_block
      art_lineno=$(grep -n "%MSG-s ArtException:" "$outfile" | awk -F: -v e="$exit_lineno" '$1<e {l=$1} END{print l}') || true
      exc_block=""
      if [[ -n "$art_lineno" ]]; then
        exc_block=$(awk -v s="$art_lineno" -v e="$exit_lineno" 'NR>=s && NR<=e {print "out:" NR ":" $0}' "$outfile")
      fi
      if [[ -n "$errfile" ]]; then
        local last_lar_lineno err_lines
        last_lar_lineno=$(grep -n "^Running: lar -c" "$errfile" | tail -1 | cut -d: -f1) || true
        if [[ -n "$last_lar_lineno" ]]; then
          err_lines=$(grep -inE "error|exception|traceback|not found|failed|abort|segmentation" "$errfile" \
            | awk -F: -v start="$last_lar_lineno" '$1+0 >= start' \
            | grep -viE "cvmfs info repo|switched to catalog|ifdh: config|ifdh constructor|^[0-9]+:inhaled environment:" \
            | sed -E "s/^([0-9]+)/err:\1/") || true
          exc_block+=$'\n'"$err_lines"
        fi
      fi
      exc_block=$(dedupe <<< "$exc_block" | sed '/^$/d')
      if [[ -n "$exc_block" ]]; then
        echo
        echo "-- Full ArtException + corresponding stderr leading up to nonzero exit (status ${exit_code}) --"
        echo "$exc_block"
      fi
    fi
  fi

  echo
  echo "-- SAM consumer / retry signatures --"
  case "$(basename "$outfile")" in
    *.v[0-9]*.out) echo "NOTE: $(basename "$outfile") is a RETRY attempt of this node (condor restarted it);"
                   echo "      the un-suffixed log is the earlier attempt -- read both." ;;
  esac
  block=$( { grep_tag out "$outfile" -E "executable was killed|is in state bad|cannot restart it|Got consumerid of: *$|should follow immediately after the equal sign"
             grep_tag err "$errfile" -E "executable was killed|is in state bad|cannot restart it|Got consumerid of: *$|should follow immediately after the equal sign"
             grep_tag err "$errfile" -oE "sam-process-id= " 
           } | dedupe ) || true
  echo "${block:-(none found)}"
  if grep -qE "is in state bad|Got consumerid of: *$" "$outfile" "$errfile" 2>/dev/null; then
    echo "=> retry died at SAM consumer setup: an earlier attempt's SAM process went 'bad' (usually"
    echo "   killed -- check condor LastHoldReason, e.g. Docker memory limit). Real cause is the FIRST attempt."
  fi

  echo
  echo "-- Per-stage event summary --"
  block=$(grep_tag out "$outfile" -E "TrigReport Events total" | dedupe) || true
  echo "${block:-(none found)}"

  echo
  echo "-- Per-stage memory consumption --"
  block=$(grep_tag out "$outfile" -E "MemReport  VmPeak" | dedupe) || true
  echo "${block:-(none found)}"

  echo
  echo "-- Disk usage (output dir size) --"
  local du_line
  du_line=$(awk '/Here is the contents of the dir BEFORE renaming/{f=1; next} f && NF>0 {print; exit}' "$outfile") || true
  echo "${du_line:-(not found)}"

  echo
  echo "-- Metadata postscript (declare/copy-back to SAM/pnfs) --"
  if grep -q "Validating metadata" "$outfile"; then
    local n_invalid
    n_invalid=$(grep -cE "Metadata is invalid|Invalid JSON data" "$outfile" || true)
    if [[ "${n_invalid:-0}" -gt 0 ]]; then
      echo "METADATA VALIDATION FAILED (${n_invalid} hits) -- declare-file/ifdh cp SKIPPED for those outputs. Unique reasons:"
      grep -A6 -E "Metadata is invalid|Invalid JSON data" "$outfile" | grep -v "^--$" | sed '/^$/d' | sort -u
      echo
      echo "-- Example metadata JSON (first output file, for inspecting the reported error) --"
      local json_marker_lines
      mapfile -t json_marker_lines < <(grep -n "Here is the .* json\.\.\." "$outfile" | head -2 | cut -d: -f1)
      if [[ ${#json_marker_lines[@]} -eq 2 ]]; then
        awk -v s="${json_marker_lines[0]}" -v e="${json_marker_lines[1]}" 'NR>s && NR<e' "$outfile"
      else
        echo "(couldn't find two 'Here is the ... json...' markers to bound the block)"
      fi
    else
      echo "Metadata validated OK."
    fi

    echo
    block=$(grep_tag out "$outfile" -E "COPIED .* file|COPYING .* file|Dataset definition .* has been created with id") || true
    block=$(dedupe <<< "$block" | sed '/^$/d')
    if [[ -z "$block" ]]; then
      echo "-- No copy-back/declare-file/create-definition messages found --"
      echo "No output was actually declared or copied back, even though fife_wrap may still exit 0."
      if [[ -n "$errfile" ]]; then
        local postscript_lineno err_lines
        postscript_lineno=$(grep -n "leaving file loop:" "$errfile" | tail -1 | cut -d: -f1) || true
        if [[ -n "$postscript_lineno" ]]; then
          err_lines=$(grep -inE "error|exception|traceback|not found|failed|abort|segmentation" "$errfile" \
            | awk -F: -v start="$postscript_lineno" '$1+0 >= start' \
            | grep -viE "cvmfs info repo|switched to catalog|ifdh: config|ifdh constructor| -- error text:|^[0-9]+:inhaled environment:" \
            | sed -E "s/^([0-9]+)/err:\1/") || true
          block=$(dedupe <<< "$err_lines" | sed '/^$/d')
        fi
      fi
      echo "${block}"
    else
      local defs other
      defs=$(grep -E "Dataset definition .* has been created with id" <<< "$block") || true
      if [[ -n "$defs" ]]; then
        printf '%-10s %-75s %s\n' "TIER" "DEFINITION NAME" "ID"
        while IFS= read -r line; do
          local content name id tier
          content=${line#out:*:}
          name=$(sed -E "s/^Dataset definition '([^']+)'.*/\1/" <<< "$content")
          id=$(sed -E "s/.*created with id ([0-9]+).*/\1/" <<< "$content")
          tier=$(sed -E "s/^.*_([A-Za-z0-9]+)_sbnd$/\1/" <<< "$name")
          printf '%-10s %-75s %s\n' "$tier" "$name" "$id"
        done <<< "$defs"
      fi
      other=$(grep -vE "Dataset definition .* has been created with id" <<< "$block") || true
      if [[ -n "$other" ]]; then
        [[ -n "$defs" ]] && echo
        echo "$other"
      fi
    fi
  else
    echo "(no 'Validating metadata' line in .out -- postscript didn't run, or this cfg has no metadata postscript stage)"
  fi
}

# ---- mode dispatch ----
if [[ "$1" == "--offline" ]]; then
  shift
  [[ $# -ge 1 ]] || usage
  outfile=$1
  errfile=${2:-${outfile%.out}.err}
  [[ -f "$outfile" ]] || { echo "out file not found: $outfile" >&2; exit 1; }
  [[ -f "$errfile" ]] || errfile=""
  set -eo pipefail  # no UPS sourcing in this branch -- safe from here on
  analyze_one "$outfile" "$errfile"
  exit 0
fi

jobsub_id=$1
outdir=${2:-$DEFAULT_OUTDIR}

# See "DAG-job log layout" note above and REFERENCE.md.
if [[ "$jobsub_id" != *.*@* ]]; then
  jobsub_id="${jobsub_id/@/.0@}"
  echo "Note: normalized jobsub id (needs a .<process> suffix) -> ${jobsub_id}"
fi

if [[ $TOKEN_MODE == production ]]; then
  echo "== Minting production-role token (sbndpro managed-token credkey) =="
  cd "$POMS_AUTO_SUBMIT_DIR" || exit 1
  # shellcheck disable=SC1091
  source setup.sh >/tmp/sbnd_poms_setup_$$.log 2>&1
  setup_rc=$?
  if [[ $setup_rc -ne 0 ]]; then
    echo "setup.sh failed (exit $setup_rc) -- see /tmp/sbnd_poms_setup_$$.log" >&2
    exit 1
  fi
else
  echo "== Using your own (personal) token, e.g. for HNL jobs submitted under it =="
fi
source /cvmfs/fermilab.opensciencegrid.org/products/common/etc/setups.sh
setup jobsub_client >/tmp/sbnd_poms_jobsub_setup_$$.log 2>&1
jobsub_setup_rc=$?
if [[ $jobsub_setup_rc -ne 0 ]]; then
  echo "setup jobsub_client failed (exit $jobsub_setup_rc) -- see /tmp/sbnd_poms_jobsub_setup_$$.log" >&2
  exit 1
fi

# All UPS sourcing is done -- safe to turn on strict mode now.
set -eo pipefail

echo "== Fetching logs for ${jobsub_id} into ${outdir} =="
jobdir="${outdir}/${jobsub_id}"
mkdir -p "$jobdir"
jobsub_fetchlog -G sbnd -J "${jobsub_id}" --unzipdir="$jobdir"
# jobsub_fetchlog prints a harmless "KeyError: 'OTEL_EXPORTER_JAEGER_ENDPOINT'"
# traceback from jobsub_lite's tracing import -- ignore it, it still fetches.

# Rename fife_wrap<timestamp><hash>cluster.<clusterid>.<process>.{out,err}
# -> <clusterid>.<process>.{out,err}
for f in "$jobdir"/fife_wrap*cluster.*.out "$jobdir"/fife_wrap*cluster.*.err; do
  [[ -e "$f" ]] || continue
  base=$(basename "$f")
  mv "$f" "${jobdir}/${base#*cluster.}"
done

mapfile -t outfiles < <(find "$jobdir" -maxdepth 1 -name "*.out" | sort)
if [[ ${#outfiles[@]} -eq 0 ]]; then
  echo "No .out file found in ${jobdir} yet -- this is a DAG-style submission"
  echo "and the lar-stage node may still be running (only dagbegin/dagend/"
  echo "sambegin/samend bookkeeping fetched so far). Contents so far:"
  ls -la "$jobdir"
  exit 0
fi

# jobsub_fetchlog on a DAG node returns the whole DAG. Analyze only the requested
# node's logs (<cluster>.<proc>.out and its retries <cluster>.<proc>.vN.out)
# unless --all is given; never read whole-DAG logs by default (LOGS.md).
if [[ $ANALYZE_ALL -eq 0 ]]; then
  want="${jobsub_id%%@*}"
  mapfile -t mine < <(printf '%s\n' "${outfiles[@]}" | grep -E "/${want//./\\.}\.(v[0-9]+\.)?out$" || true)
  if [[ ${#mine[@]} -gt 0 && ${#mine[@]} -lt ${#outfiles[@]} ]]; then
    echo "Note: fetch returned ${#outfiles[@]} logs (whole DAG); analyzing only ${want} (${#mine[@]} file(s)). Use --all for the rest."
    outfiles=("${mine[@]}")
  fi
fi

for outfile in "${outfiles[@]}"; do
  errfile="${outfile%.out}.err"
  [[ -f "$errfile" ]] || errfile=""
  analyze_one "$outfile" "$errfile"
done
