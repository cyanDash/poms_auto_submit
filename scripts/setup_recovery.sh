#!/usr/bin/env bash

INPUT="aurora_SBND2026A_gen2_BNBLight_DevSample_prodgenie_corsika_proton_rockbox0p1_sbnd_CV_v10_14_02_03_reco1_sbnd"
CAMPAIGN="SBND2026A_gen2_PDS_DetVar_Sample3"
CAMPAIGN_TYPE="mc"
stage=flatcaf
OUTPUT="mc_SBND2026A_prodgenie_corsika_proton_rockbox0p1_sbnd_PDS_detvar_Sample3_v10_14_02_0602_${stage}_sbnd"

samweb create-definition ${INPUT}_${CAMPAIGN}_recovery_campaign "defname: ${INPUT} and not isparentof: (defname: ${OUTPUT})"
#echo "Recovery dataset: ${INPUT}_${CAMPAIGN}_recovery_campaign"
if [[ ${CAMPAIGN_TYPE} = "data" ]]; then
	echo "Prestaging..."
	samweb prestage-dataset --parallel=5 --defname="${INPUT}_${CAMPAIGN}_recovery_campaign"
	echo "Prestaging done!"
fi
