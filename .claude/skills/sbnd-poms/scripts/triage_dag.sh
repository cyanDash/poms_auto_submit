#!/bin/bash
# Triage a finished (or running) DAG submission from condor history alone --
# no log fetch. One condor_history call returns one row per DAG node; rows are
# bucketed by outcome and tallied per site, so you can pick a few
# representative jobs to fetch/analyze instead of looking at hundreds.
#
#   triage_dag.sh <dag_id>@<schedd> [outdir]
#   e.g. triage_dag.sh 30584621@jobsub05.fnal.gov
#
# Buckets (worker nodes only; sambegin/samend bookkeeping nodes are dropped):
#   ok           JobStatus 4, ExitCode 0
#   early_death  nonzero exit, wall time < EARLY_S (default 120 s). Usually no
#                fife_wrap log exists (died before fife_wrap started) --
#                the ExitCode here is all you get. 127 = command not found.
#   late_fail    nonzero exit after running >= EARLY_S; logs should exist
#   held         JobStatus 5 (see HoldReason column)
#   removed      JobStatus 3 (see RemoveReason column)
#   active       still idle/running
#
# Writes <outdir>/<dag_id>_history.tsv (one row per worker) and prints a
# bucket x site table with one example job id per cell. Default outdir:
# /tmp/triage.
#
# condor_history needs a valid token; if the first call returns nothing the
# script mints the sbndpro production token (poms_auto_submit setup.sh) and
# retries. No strict mode: UPS setups.sh breaks under set -e/-u (REFERENCE.md).

# Repo root is four levels up: <root>/.claude/skills/sbnd-poms/scripts/.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
POMS_AUTO_SUBMIT_DIR=$(cd "$HERE/../../../.." && pwd -P)
POOL=gpcollector03.fnal.gov
EARLY_S=${EARLY_S:-120}

[[ $# -ge 1 && "$1" == *@* ]] || { echo "Usage: $0 <dag_id>@<schedd> [outdir]" >&2; exit 1; }
dag=${1%%@*}; dag=${dag%%.*}
schedd=${1#*@}
outdir=${2:-/tmp/triage}
mkdir -p "$outdir" || exit 1
tsv="$outdir/${dag}_history.tsv"
raw="$outdir/${dag}_history.raw"

query() {
  condor_history -pool "$POOL" -name "$schedd" -constraint "DAGManJobId==${dag}" \
    -af:t ClusterId ProcId ExitCode ExitBySignal JobStatus RemoteWallClockTime \
          MATCH_GLIDEIN_Site LastRemoteHost ResidentSetSize NumJobStarts Out \
          HoldReason RemoveReason LastHoldReason LastVacateTime > "$raw" 2>"$raw.err"
}
query
if [[ ! -s "$raw" ]]; then
  echo "No rows from condor_history -- minting production token and retrying" >&2
  ( cd "$POMS_AUTO_SUBMIT_DIR" && source setup.sh >/tmp/sbnd_poms_setup_$$.log 2>&1 ) \
    || { echo "setup.sh failed, see /tmp/sbnd_poms_setup_$$.log" >&2; exit 1; }
  # token file is written by setup.sh; re-source in this shell for env vars
  ( cd "$POMS_AUTO_SUBMIT_DIR" && source setup.sh >/dev/null 2>&1; query )
  [[ -s "$raw" ]] || { echo "Still no rows for DAG ${dag}@${schedd}; see $raw.err" >&2; exit 1; }
fi

# Header + classified rows. Columns:
# bucket cluster proc exit sig status wall_s site host rss_mb starts hold remove lasthold
# (lasthold = LastHoldReason: the hold that preceded a retry, e.g. Docker memory limit;
#  HoldReason is only set while the job is still held)
{
  printf 'bucket\tcluster\tproc\texit\tsignal\tstatus\twall_s\tsite\thost\trss_mb\tstarts\thold\tremove\tlasthold\n'
  awk -F'\t' -v early="$EARLY_S" '
    $11 ~ /^(sambegin|samend|dagbegin|dagend)/ { next }
    {
      b = "active"
      if ($5 == 4 && $3 == 0)            b = "ok"
      else if ($5 == 4 && $3 != 0)       b = ($6 < early ? "early_death" : "late_fail")
      else if ($5 == 5)                  b = "held"
      else if ($5 == 3)                  b = "removed"
      printf "%s\t%s\t%s\t%s\t%s\t%s\t%d\t%s\t%s\t%d\t%s\t%s\t%s\t%s\n",
             b, $1, $2, $3, $4, $5, $6, $7, $8, $9/1024, $10, $12, $13, $14
    }' "$raw"
} > "$tsv"

echo "== DAG ${dag}@${schedd}: $(($(wc -l < "$tsv") - 1)) worker nodes  ->  $tsv =="
echo
echo "-- bucket x site  (count, example <cluster>.<proc>) --"
awk -F'\t' 'NR>1 { k=$1 "\t" $8; n[k]++; if (!(k in ex)) ex[k]=$2 "." $3; c[k "\t" $4]++ }
  END {
    for (k in n) {
      split(k, a, "\t"); cs = ""
      for (ck in c) { split(ck, b, "\t"); if (b[1]==a[1] && b[2]==a[2] && a[1]!="ok") cs = cs " exit" b[3] "x" c[ck] }
      printf "%-12s %-14s %5d  e.g. %s%s\n", a[1], a[2], n[k], ex[k], cs
    }
  }' "$tsv" | sort
echo
echo "-- hosts behind early_death (top 10) --"
awk -F'\t' 'NR>1 && $1=="early_death" { sub(/^.*@/, "", $9); n[$9]++ } END { for (h in n) print n[h], h }' "$tsv" | sort -rn | head -10
echo
echo "-- late_fail wall time (s): min / median / max --"
awk -F'\t' 'NR>1 && $1=="late_fail" { print $7 }' "$tsv" | sort -n | awk '{a[NR]=$1} END { if (NR) printf "%d / %d / %d  (n=%d)\n", a[1], a[int((NR+1)/2)], a[NR], NR; else print "none" }'
echo
echo "-- retried workers (NumJobStarts > 1): $(awk -F'\t' 'NR>1 && $11>1' "$tsv" | wc -l) --"
echo "   by LastHoldReason (why the first attempt died; count, reason, buckets):"
awk -F'\t' 'NR>1 && $11>1 { r=$14; if (r=="" || r=="undefined") r="(none)"; k=r; n[k]++; b[k]=b[k] " " $1 }
  END { for (k in n) printf "   %4d  %s  [%s ]\n", n[k], k, b[k] }' "$tsv" | sort -rn
echo "   (condor ExitCode is from the LAST attempt; a fetched log from an earlier attempt can"
echo "    show fife_wrap exit 1 while condor says exit 0)"
