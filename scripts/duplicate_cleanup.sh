#!/usr/bin/env bash
#
# Submits duplicate-cleanup jobs for each given SAM dataset definition.
# Called by scripts/cleanup.py; see TODO.md's File Cleanup feature.
#
# Usage: duplicate_cleanup.sh <outdir> <dataset> [dataset ...]

SAM_DELETE_DUPLICATES=/exp/sbnd/app/users/sbndpro/mcp/MCP2025Av3/srcs/sbnutil/scripts/sbnd/sam_delete_duplicates_mateusc-v2.py

OUTDIR=$1
shift

for OUTPUT in "$@"; do
	if samweb describe-definition "$OUTPUT" &> /dev/null ; then
		python "$SAM_DELETE_DUPLICATES" --dataset "$OUTPUT" --delete &>> "${OUTDIR}/${OUTPUT}_cleanup.log" &
		echo "Dataset: ${OUTPUT} submitted for cleanup"
	fi
done
