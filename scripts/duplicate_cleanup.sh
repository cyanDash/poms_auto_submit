#!/usr/bin/env bash

# Submits duplicate cleanup jobs for the SBND PDS DetVar sample stages and stores the logs.


OUTDIR="/exp/sbnd/app/users/sbndpro/mcp/sdas1/PDS_Detvar3/cleanup_logs"    # Directory where the logfiles will be stored

for stage in flatcaf caf histreco2 reco2 larcvreco1 reco1; do
        ################################################################################
        OUTPUT="mc_SBND2026A_prodgenie_corsika_proton_rockbox0p1_sbnd_PDS_detvar_Sample3_v10_14_02_0602_${stage}_sbnd"
        ################################################################################
	if samweb describe-definition $OUTPUT &> /dev/null ; then
		python /exp/sbnd/app/users/sbndpro/mcp/MCP2025Av3/srcs/sbnutil/scripts/sbnd/sam_delete_duplicates_mateusc-v2.py --dataset $OUTPUT --delete &>> ${OUTDIR}/${stage}_cleanup.log &
		echo "Stage: ${stage} submitted for cleanup"
	fi
done
