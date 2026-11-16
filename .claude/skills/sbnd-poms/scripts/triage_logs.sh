#!/bin/bash
# Classify many fetched fife_wrap logs without reading them whole.
#
#   triage_logs.sh <logdir> [STAGES]
#   e.g. triage_logs.sh .../30584621.0@jobsub05.fnal.gov gen,g4,torch,triton
#
# For every <cluster>.<proc>.out in <logdir> (analyze_job/fetch_and_analyze
# naming), reports:
#   exit        fife_wrap "COMPLETED with exit status N" (last one), or NONE
#   done        number of "Art has completed" lines = lar stages that finished
#   killed      1 if "executable was killed" appears
#   died_in     name of the stage that was running (STAGES[done], 0-based) --
#               only filled for nonzero/NONE exits
# Exit-0 jobs are dropped from the detail list. Prints a summary of
# (exit, died_in) groups with one example file each, and writes the full
# table to <logdir>/triage_logs.tsv.
#
# STAGES is the ordered lar-stage list of the cfg (default stage1,stage2,...).
# It only labels the output; the classification itself needs no knowledge of
# the job. Pair with triage_dag.sh: that finds *which* jobs failed without
# logs; this classifies the ones that did leave logs.

[[ -d "$1" ]] || { echo "Usage: $0 <logdir> [stage1,stage2,...]" >&2; exit 1; }
dir=$1
IFS=, read -ra stages <<< "${2:-}"
out="$dir/triage_logs.tsv"
printf 'file\texit\tdone\tkilled\tdied_in\n' > "$out"

for f in "$dir"/[0-9]*.out; do
  [[ -e "$f" ]] || continue
  line=$(grep -a 'COMPLETED with exit status' "$f" | tail -1)
  ec=${line##*exit status }
  [[ -n "$line" ]] || ec=NONE
  done_n=$(grep -ac 'Art has completed' "$f")
  killed=$(grep -ac 'executable was killed' "$f")
  died=""
  if [[ "$ec" != 0 ]]; then
    if [[ ${#stages[@]} -gt 0 && -n "${stages[$done_n]:-}" ]]; then died=${stages[$done_n]}
    else died="stage$((done_n + 1))"; fi
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$(basename "$f")" "$ec" "$done_n" "$killed" "$died" >> "$out"
done

echo "== $(($(wc -l < "$out") - 1)) logs in $dir  ->  $out =="
awk -F'\t' 'NR>1 { k=$2 "\t" $5; n[k]++; if (!(k in ex)) ex[k]=$1 }
  END { for (k in n) { split(k,a,"\t"); printf "exit=%-5s died_in=%-10s %5d  e.g. %s\n", a[1], (a[2]==""?"-":a[2]), n[k], ex[k] } }' "$out" | sort
