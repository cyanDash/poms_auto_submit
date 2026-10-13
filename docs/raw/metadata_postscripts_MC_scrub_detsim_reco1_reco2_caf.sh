#!/bin/bash

# =============================================================================
# metadata_postscripts_MC_scrub_detsim_reco1_reco2_caf.sh
#
# Metadata postscript for the MC scrub_detsim_reco1_reco2_caf POMS stage.
#
# Runs AFTER all executables (scrub + detsim + reco1 + reco2 + caf) finish.
#
# Adapted from:
#   metadata_postscripts_MC_onestage.sh  (handles reco1/larcv/hist/caf/flat)
#   metadata_postscripts_MC_reco2-caf.sh (cleaner structure and comments)
#
# Key differences from metadata_postscripts_MC_onestage.sh:
#   - PARENT_FILE_SAM is the scrub-input artroot file (set by metadata_prescripts.sh)
#   - Scrub and detsim intermediates are NOT saved (they are discarded)
#   - reco1 saving is conditional on SAVE_RECO1 env var (default: 1)
#   - larcv saving is conditional on SAVE_LARCV env var (default: 1)
#   - hist/caf/flatcaf are always saved
#   - event_count is read from the hist JSON sidecar, derived from histFile
#
# ON/OFF SWITCH:
#   SAVE_RECO1 and SAVE_LARCV are read from the environment.
#   Set them in the cfg [env_pass] section (1=save, 0=skip).
#   Defaults to 1 (save) if not set.
#
# What this script does, in order:
#   1. Find output files (reco1, larcv, hist, caf, flatcaf)
#   2. Read run/subrun/event info from the messages log
#   3. Read event_count from the hist JSON sidecar
#   4. Rename files with unique hashes (avoids filename collisions in dCache)
#   5. Build a SAMWeb JSON metadata file for each output
#   6. Retire any previously declared file with the same name (safety)
#   7. Validate metadata with SAMWeb
#   8. Copy, declare, and add locations to SAMWeb
#   9. Create SAMWeb dataset definitions
#
# Steps 4-9 are skipped for reco1/larcv when SAVE_RECO1=0 / SAVE_LARCV=0.
# =============================================================================

>&2 echo -e "\n\nSTARTING $0...\n\n"

# PARENT_FILE_SAM is set by metadata_prescripts.sh (run during job setup).
# It contains the SAM filename of the scrub-input artroot file for this job.
# All outputs list this file as their parent.
initialFile=$(basename ${PARENT_FILE_SAM})

# On/off switches (read from env, default to 1 = save)
SAVE_RECO1=${SAVE_RECO1:-1}
SAVE_LARCV=${SAVE_LARCV:-1}

# Output file variables -- populated by find commands below
reco1File=''   # Artroot reco1 file
larcvFile=''   # LarCV file (secondary stream from reco1 step)
histFile=''    # Calib ntuples (hist_ from reco2 via -T)
cafFile=''     # CAF tree
flatFile=''    # Flat CAF tree

>&2 echo -e "\n\nUsing fcl list $1...\n\n"
>&2 echo -e "\n\nParent scrub-input file: ${initialFile}\n\n"
>&2 echo -e "\n\nSAVE_RECO1=${SAVE_RECO1}  SAVE_LARCV=${SAVE_LARCV}\n\n"

# =============================================================================
# STEP 1: Find output files
#
# We use find + grep to locate each file type by naming convention.
# Scrub and detsim intermediates (scrub.root, scrub_detsim.root) are not
# explicitly handled here -- they are simply left in the working directory
# and discarded when the job exits.
# =============================================================================

# reco1 file: scrub_detsim_reco1.root (contains 'reco1', excludes hist/larcv/reco2/caf)
reco1File=$(find ./ -iname '*.root' | grep 'reco1' | grep -v 'larcv' | grep -v 'hist' | grep -v 'reco2' | grep -v 'caf')

# larcv file: secondary output stream from reco1 step, named larcv*.root
larcvFile=$(find ./ -iname 'larcv*.root' | grep -v 'reco2')

# hist file: produced by reco2 via the -T flag (TFileService output)
# grep 'reco2' distinguishes it from hist_scrub.root, hist_scrub_detsim.root, etc.
histFile=$(find ./ -iname 'hist*.root' | grep 'reco2' | head -n 1)

# CAF file: ends in caf.root but NOT flat.caf.root
cafFile=$(find ./ -iname '*caf.root' | grep -v 'flat' | grep -v 'hist' | grep -v 'reco')

# Flat CAF file: ends in flat.caf.root or .flat.caf.root
flatFile=$(find ./ -iname '*.caf.root' | grep 'flat')

echo -e "Here is the contents of the dir BEFORE renaming..."
ls -ltrh

echo -e "reco1File : ${reco1File}"
echo -e "larcvFile : ${larcvFile}"
echo -e "histFile  : ${histFile}"
echo -e "cafFile   : ${cafFile}"
echo -e "flatFile  : ${flatFile}"

# =============================================================================
# STEP 2: Get run/subrun/event info from the messages log
# =============================================================================
source_log=messages.log

first_token=$(cat ${source_log} | grep -e 'run:' | grep -e 'event:' | grep -v '.cc' | awk -F " run: " '{print $2}' | sed -e 's/^ //' | sort -u | grep -e 'event' | sort -k5,5n  | head -n 1)
last_token=$( cat ${source_log} | grep -e 'run:' | grep -e 'event:' | grep -v '.cc' | awk -F " run: " '{print $2}' | sed -e 's/^ //' | sort -u | grep -e 'event' | sort -k5,5nr | head -n 1)

run_no=$(    echo ${first_token} | awk -F " " '{print $1}')
subrun_no=$( echo ${first_token} | awk -F " " '{print $3}')
first_event=$(echo ${first_token} | awk -F " " '{print $5}')
last_event=$( echo ${last_token}  | awk -F " " '{print $5}')

# =============================================================================
# STEP 3: Get event_count from the hist JSON sidecar
#
# sbnpoms_metadata_injector.sh (prescript_10) produces this JSON sidecar
# alongside the hist_ file from the reco2 step, named <histFile>.json.
# Derived from histFile (rather than hardcoded) so this script works
# regardless of how many intermediate stages (e.g. g4) precede reco2.
# =============================================================================
json_file="${histFile}.json"
event_count=$(grep '"event_count":' "$json_file" | head -1 | sed 's/.*"event_count": *//' | sed 's/[^0-9].*//')

# The fcl list is passed as argument $1 from the cfg postscript line
fcl_list=$1

# =============================================================================
# STEP 4: Compute md5 hashes and build unique filenames
#
# Files are renamed to include an md5-based UUID to guarantee globally unique
# filenames in dCache and SAMWeb. The hash is derived from the file content
# plus some $RANDOM salt.
#
# Format: <base_name>-<UUID>.root   (UUID = 8-4-4-4-12 hex groups)
# First 2 hex chars of UUID are used as a dCache subdirectory prefix.
# =============================================================================

# --- Always compute hashes for hist, caf, flat ---
echo $RANDOM > hmd.log ; echo $RANDOM >> hmd.log ; echo $RANDOM >> hmd.log
md5_hist=$(md5sum ${histFile} | awk -F " " '{print $1}')
echo $RANDOM > hmd.log ; echo $RANDOM >> hmd.log ; echo $RANDOM >> hmd.log
md5_caf=$(md5sum ${cafFile}  | awk -F " " '{print $1}')
echo $RANDOM > hmd.log ; echo $RANDOM >> hmd.log ; echo $RANDOM >> hmd.log
md5_flat=$(md5sum ${flatFile} | awk -F " " '{print $1}')

# --- Conditionally compute hashes for reco1, larcv ---
if [[ ${SAVE_RECO1} == 1 ]]; then
    echo $RANDOM > hmd.log ; echo $RANDOM >> hmd.log ; echo $RANDOM >> hmd.log
    md5_reco1=$(md5sum ${reco1File} | awk -F " " '{print $1}')
fi

if [[ ${SAVE_LARCV} == 1 ]]; then
    echo $RANDOM > hmd.log ; echo $RANDOM >> hmd.log ; echo $RANDOM >> hmd.log
    md5_larcv=$(md5sum ${larcvFile} | awk -F " " '{print $1}')
fi

# --- Strip any existing UUID hash from filename (in case of reprocessing) ---
old_hist_hash=$(echo ${histFile}  | sed -E 's/.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/\1/')
excised_histFile=${histFile%-${old_hist_hash}*}
old_caf_hash=$(echo ${cafFile}    | sed -E 's/.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/\1/')
excised_cafFile=${cafFile%-${old_caf_hash}*}
old_flat_hash=$(echo ${flatFile}  | sed -E 's/.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/\1/')
excised_flatFile=${flatFile%-${old_flat_hash}*}

if [[ ${SAVE_RECO1} == 1 ]]; then
    old_reco1_hash=$(echo ${reco1File} | sed -E 's/.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/\1/')
    excised_reco1File=${reco1File%-${old_reco1_hash}*}
fi

if [[ ${SAVE_LARCV} == 1 ]]; then
    old_larcv_hash=$(echo ${larcvFile} | sed -E 's/.*-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/\1/')
    excised_larcvFile=${larcvFile%-${old_larcv_hash}*}
fi

# --- Format md5 as UUID (8-4-4-4-12) ---
used_hist_hash=${md5_hist:0:8}-${md5_hist:8:4}-${md5_hist:12:4}-${md5_hist:16:4}-${md5_hist:20:12}
used_caf_hash=${md5_caf:0:8}-${md5_caf:8:4}-${md5_caf:12:4}-${md5_caf:16:4}-${md5_caf:20:12}
used_flat_hash=${md5_flat:0:8}-${md5_flat:8:4}-${md5_flat:12:4}-${md5_flat:16:4}-${md5_flat:20:12}

used_hist_prebit=${used_hist_hash:0:2}
used_caf_prebit=${used_caf_hash:0:2}
used_flat_prebit=${used_flat_hash:0:2}

if [[ ${SAVE_RECO1} == 1 ]]; then
    used_reco1_hash=${md5_reco1:0:8}-${md5_reco1:8:4}-${md5_reco1:12:4}-${md5_reco1:16:4}-${md5_reco1:20:12}
    used_reco1_prebit=${used_reco1_hash:0:2}
fi

if [[ ${SAVE_LARCV} == 1 ]]; then
    used_larcv_hash=${md5_larcv:0:8}-${md5_larcv:8:4}-${md5_larcv:12:4}-${md5_larcv:16:4}-${md5_larcv:20:12}
    used_larcv_prebit=${used_larcv_hash:0:2}
fi

# --- Build definitive filenames ---
newHistFile=${excised_histFile%.root}-${used_hist_hash}.root
newCafFile=${excised_cafFile%.root}-${used_caf_hash}.caf.root
newFlatFile=${excised_flatFile%.root}-${used_flat_hash}.flat.caf.root

if [[ ${SAVE_RECO1} == 1 ]]; then
    newReco1File=${excised_reco1File%.root}-${used_reco1_hash}.root
fi

if [[ ${SAVE_LARCV} == 1 ]]; then
    newLarcvFile=${excised_larcvFile%.root}-${used_larcv_hash}.root
fi

# --- Rename on disk ---
mv ${histFile}  ${newHistFile}
mv ${cafFile}   ${newCafFile}
mv ${flatFile}  ${newFlatFile}

if [[ ${SAVE_RECO1} == 1 ]]; then mv ${reco1File} ${newReco1File} ; fi
if [[ ${SAVE_LARCV} == 1 ]]; then mv ${larcvFile} ${newLarcvFile} ; fi

echo -e "Definitive hist name  : ${newHistFile}"
echo -e "Definitive caf name   : ${newCafFile}"
echo -e "Definitive flat name  : ${newFlatFile}"
[[ ${SAVE_RECO1} == 1 ]] && echo -e "Definitive reco1 name : ${newReco1File}" || echo -e "reco1: SAVE_RECO1=0, skipping"
[[ ${SAVE_LARCV} == 1 ]] && echo -e "Definitive larcv name : ${newLarcvFile}" || echo -e "larcv: SAVE_LARCV=0, skipping"

# =============================================================================
# STEP 5: Build SAMWeb JSON metadata files
# =============================================================================

HIST_MD=md_hist.json
CAF_MD=md_caf.json
FLAT_MD=md_flat.json

touch ${HIST_MD}; touch ${CAF_MD}; touch ${FLAT_MD}

if [[ ${SAVE_RECO1} == 1 ]]; then RECO1_MD=md_reco1.json ; touch ${RECO1_MD} ; fi
if [[ ${SAVE_LARCV} == 1 ]]; then LARCV_MD=md_larcv.json ; touch ${LARCV_MD} ; fi

# --- Dataset tag preamble ---
export DTAG_PREAMBLE=${MT_PRODUCTIONTYPE}_${MT_PRODUCTIONLABEL}_${MT_FCLNAME}_${MT_SAMPLE}_${SBNDCODE_VERSION}
export RECO1_DTAG=${DTAG_PREAMBLE}_reco1_sbnd
export LARCV_DTAG=${DTAG_PREAMBLE}_larcvreco1_sbnd
export HIST_DTAG=${DTAG_PREAMBLE}_histreco2_sbnd
export CAF_DTAG=${DTAG_PREAMBLE}_caf_sbnd
export FLAT_DTAG=${DTAG_PREAMBLE}_flatcaf_sbnd

# Helper: build a single JSON file for one output
# Args: $1=md_file $2=filename $3=filesize $4=data_tier $5=file_format
#       $6=dataset_tag $7=process_name
build_json() {
    local mdFile=$1
    local fname=$2
    local fsize=$3
    local dtier=$4
    local ffmt=$5
    local dtag=$6
    local pname=$7

    echo -e "{"                                                                            >> ${mdFile}
    echo -e " \"file_name\": \"${fname}\","                                               >> ${mdFile}
    echo -e " \"file_size\": ${fsize},"                                                   >> ${mdFile}
    echo -e " \"data_tier\": \"${dtier}\","                                               >> ${mdFile}
    echo -e " \"file_format\": \"${ffmt}\","                                              >> ${mdFile}
    echo -e " \"checksum\": ["                                                            >> ${mdFile}
    echo -e "$(samweb -e ${SAM_EXPERIMENT} file-checksum --type=enstore,adler32,md5 ${fname} | tr -d '[' | tr -d ']' | tr ' ' '\n')" >> ${mdFile}
    echo -e " ],"                                                                         >> ${mdFile}
    echo -e " \"Dataset.Tag\": \"${dtag}\","                                              >> ${mdFile}
    echo -e " \"process_id\": ${SAM_CONSUMER_ID},"                                       >> ${mdFile}
    echo -e " \"file_type\": \"mc\","                                                     >> ${mdFile}
    echo -e " \"group\": \"sbnd\","                                                       >> ${mdFile}
    echo -e " \"application\": {\n\t\"family\": \"art\",\n\t\"name\": \"sbndcode\",\n\t\"version\": \"${SBNDCODE_VERSION}\"\n }," >> ${mdFile}
    echo -e " \"art.file_format_era\": \"ART_2011a\","                                   >> ${mdFile}
    echo -e " \"art.file_format_version\": 15,"                                          >> ${mdFile}
    echo -e " \"art.run_type\": \"physics\","                                             >> ${mdFile}
    echo -e " \"art.process_name\": \"${pname}\","                                       >> ${mdFile}
    echo -e " \"fcl.name\": \"${fcl_list}\","                                            >> ${mdFile}
    echo -e " \"production.name\": \"${MT_PRODUCTIONNAME}\","                            >> ${mdFile}
    echo -e " \"production.type\": \"${MT_PRODUCTIONTYPE}\","                            >> ${mdFile}
    echo -e " \"mc.generated_event_count\": ${MT_EVENTS_PER_JOB},"                      >> ${mdFile}
    echo -e " \"data_stream\": \"out1\","                                                 >> ${mdFile}
    echo -e " \"sbnd_project.name\": \"${MT_PROJECTNAME}\","                             >> ${mdFile}
    echo -e " \"sbnd_project.software\": \"${MT_PROJECTSOFTWARE}\","                     >> ${mdFile}
    echo -e " \"sbnd_project.stage\": \"${MT_PROJECTSTAGE}\","                           >> ${mdFile}
    echo -e " \"sbnd_project.version\": \"${SBNDCODE_VERSION}\","                        >> ${mdFile}
    echo -e " \"runs\": ["                                                                >> ${mdFile}
    echo -e "   ["                                                                        >> ${mdFile}
    echo -e "     ${run_no},"                                                             >> ${mdFile}
    echo -e "     ${subrun_no},"                                                          >> ${mdFile}
    echo -e "     \"physics\""                                                            >> ${mdFile}
    echo -e "   ]"                                                                        >> ${mdFile}
    echo -e " ],"                                                                         >> ${mdFile}
    echo -e " \"event_count\": ${event_count},"                                          >> ${mdFile}
    echo -e " \"first_event\": ${first_event},"                                          >> ${mdFile}
    echo -e " \"last_event\": ${last_event},"                                            >> ${mdFile}
    echo -e " \"parents\": ["                                                             >> ${mdFile}
    echo -e "\t{ \"file_name\": \"${PARENT_FILE_SAM}\" }"                                >> ${mdFile}
    echo -e "  ]"                                                                         >> ${mdFile}
    echo -e " }"                                                                          >> ${mdFile}
}

# Build JSON for always-saved files
build_json ${HIST_MD} "$(basename ${newHistFile})" "$(stat -c %s ${newHistFile})" "root-tuple"  "root"     "${HIST_DTAG}" "Reco2"
build_json ${CAF_MD}  "$(basename ${newCafFile})"  "$(stat -c %s ${newCafFile})"  "caf"         "caf"      "${CAF_DTAG}"  "CAF"
build_json ${FLAT_MD} "$(basename ${newFlatFile})" "$(stat -c %s ${newFlatFile})" "flat_caf"    "flat_caf" "${FLAT_DTAG}" "CAF"

# Build JSON for conditionally-saved files
if [[ ${SAVE_RECO1} == 1 ]]; then
    build_json ${RECO1_MD} "$(basename ${newReco1File})" "$(stat -c %s ${newReco1File})" "reconstructed" "artroot" "${RECO1_DTAG}" "Reco1"
fi

if [[ ${SAVE_LARCV} == 1 ]]; then
    build_json ${LARCV_MD} "$(basename ${newLarcvFile})" "$(stat -c %s ${newLarcvFile})" "larcv" "larcv" "${LARCV_DTAG}" "Reco1"
fi

# --- Print JSONs to log for inspection ---
echo -e "Here is the hist json...";  cat ${HIST_MD}  | sed '/^$/d'
echo -e "Here is the caf json...";   cat ${CAF_MD}   | sed '/^$/d'
echo -e "Here is the flat json...";  cat ${FLAT_MD}  | sed '/^$/d'
[[ ${SAVE_RECO1} == 1 ]] && { echo -e "Here is the reco1 json..."; cat ${RECO1_MD} | sed '/^$/d'; }
[[ ${SAVE_LARCV} == 1 ]] && { echo -e "Here is the larcv json..."; cat ${LARCV_MD} | sed '/^$/d'; }

echo "Here are the contents of my workdir..."
ls -ltrh

# =============================================================================
# STEP 6: Retire any previously declared files with the same name (safety)
# =============================================================================
retire_if_exists() {
    local fname=$1
    local bname=$(basename ${fname})
    local checkMeta=$(samweb -e ${SAM_EXPERIMENT} get-metadata ${bname} 2>/dev/null)
    if [[ $? == 0 ]] ; then
        samweb -e ${SAM_EXPERIMENT} retire-file ${bname}
    fi
}

retire_if_exists ${newHistFile}
retire_if_exists ${newCafFile}
retire_if_exists ${newFlatFile}
[[ ${SAVE_RECO1} == 1 ]] && retire_if_exists ${newReco1File}
[[ ${SAVE_LARCV} == 1 ]] && retire_if_exists ${newLarcvFile}

# =============================================================================
# STEP 7: Validate metadata
# =============================================================================
echo "Validating metadata..."
samweb -e ${SAM_EXPERIMENT} validate-metadata ${HIST_MD}
samweb -e ${SAM_EXPERIMENT} validate-metadata ${CAF_MD}
samweb -e ${SAM_EXPERIMENT} validate-metadata ${FLAT_MD}
[[ ${SAVE_RECO1} == 1 ]] && samweb -e ${SAM_EXPERIMENT} validate-metadata ${RECO1_MD}
[[ ${SAVE_LARCV} == 1 ]] && samweb -e ${SAM_EXPERIMENT} validate-metadata ${LARCV_MD}

# =============================================================================
# STEP 8 & 9: Copy, declare, and create dataset definitions
#
# Output directory structure under IFDH_OUTPUT_DIR:
#   reco1/   <stream>/<hash_prefix>/   -- reco1 files (if SAVE_RECO1=1)
#   larcv/   <stream>/<hash_prefix>/   -- larcv files (if SAVE_LARCV=1)
#   calib/   <stream>/<hash_prefix>/   -- hist_ files
#   caf/     <stream>/<hash_prefix>/   -- caf files
#   flatcaf/ <stream>/<hash_prefix>/   -- flat caf files
# =============================================================================

FULL_HIST_OUTDIR=${IFDH_OUTPUT_DIR}/calib/${MT_SBND_STREAM_NAME}/${used_hist_prebit}/
FULL_CAF_OUTDIR=${IFDH_OUTPUT_DIR}/caf/${MT_SBND_STREAM_NAME}/${used_caf_prebit}/
FULL_FLAT_OUTDIR=${IFDH_OUTPUT_DIR}/flatcaf/${MT_SBND_STREAM_NAME}/${used_flat_prebit}/

###### HIST (calib ntuple) DECLARATION ######
if samweb -e ${SAM_EXPERIMENT} validate-metadata ${HIST_MD} ; then
    ifdh cp -D ${newHistFile} ${FULL_HIST_OUTDIR} && echo "COPIED hist file to ${FULL_HIST_OUTDIR}"
    samweb -e ${SAM_EXPERIMENT} declare-file ${HIST_MD}
    samweb -e ${SAM_EXPERIMENT} add-file-location $(basename ${newHistFile}) dcache:${FULL_HIST_OUTDIR}
    samweb -e ${SAM_EXPERIMENT} create-definition ${HIST_DTAG} "Dataset.Tag ${HIST_DTAG}"
fi

###### CAF DECLARATION ######
if samweb -e ${SAM_EXPERIMENT} validate-metadata ${CAF_MD} ; then
    ifdh cp -D ${newCafFile} ${FULL_CAF_OUTDIR} && echo "COPIED caf file to ${FULL_CAF_OUTDIR}"
    samweb -e ${SAM_EXPERIMENT} declare-file ${CAF_MD}
    samweb -e ${SAM_EXPERIMENT} add-file-location $(basename ${newCafFile}) dcache:${FULL_CAF_OUTDIR}
    samweb -e ${SAM_EXPERIMENT} create-definition ${CAF_DTAG} "Dataset.Tag ${CAF_DTAG}"
fi

###### FLAT CAF DECLARATION ######
if samweb -e ${SAM_EXPERIMENT} validate-metadata ${FLAT_MD} ; then
    ifdh cp -D ${newFlatFile} ${FULL_FLAT_OUTDIR} && echo "COPIED flatcaf file to ${FULL_FLAT_OUTDIR}"
    samweb -e ${SAM_EXPERIMENT} declare-file ${FLAT_MD}
    samweb -e ${SAM_EXPERIMENT} add-file-location $(basename ${newFlatFile}) dcache:${FULL_FLAT_OUTDIR}
    samweb -e ${SAM_EXPERIMENT} create-definition ${FLAT_DTAG} "Dataset.Tag ${FLAT_DTAG}"
fi

###### RECO1 DECLARATION (conditional) ######
if [[ ${SAVE_RECO1} == 1 ]]; then
    FULL_RECO1_OUTDIR=${IFDH_OUTPUT_DIR}/reco1/${MT_SBND_STREAM_NAME}/${used_reco1_prebit}/
    if samweb -e ${SAM_EXPERIMENT} validate-metadata ${RECO1_MD} ; then
        ifdh cp -D ${newReco1File} ${FULL_RECO1_OUTDIR} && echo "COPIED reco1 file to ${FULL_RECO1_OUTDIR}"
        samweb -e ${SAM_EXPERIMENT} declare-file ${RECO1_MD}
        samweb -e ${SAM_EXPERIMENT} add-file-location $(basename ${newReco1File}) dcache:${FULL_RECO1_OUTDIR}
        samweb -e ${SAM_EXPERIMENT} create-definition ${RECO1_DTAG} "Dataset.Tag ${RECO1_DTAG}"
    fi
else
    echo "SAVE_RECO1=0: skipping reco1 copy/declare"
fi

###### LARCV DECLARATION (conditional) ######
if [[ ${SAVE_LARCV} == 1 ]]; then
    FULL_LARCV_OUTDIR=${IFDH_OUTPUT_DIR}/larcv/${MT_SBND_STREAM_NAME}/${used_larcv_prebit}/
    if samweb -e ${SAM_EXPERIMENT} validate-metadata ${LARCV_MD} ; then
        ifdh cp -D ${newLarcvFile} ${FULL_LARCV_OUTDIR} && echo "COPIED larcv file to ${FULL_LARCV_OUTDIR}"
        samweb -e ${SAM_EXPERIMENT} declare-file ${LARCV_MD}
        samweb -e ${SAM_EXPERIMENT} add-file-location $(basename ${newLarcvFile}) dcache:${FULL_LARCV_OUTDIR}
        samweb -e ${SAM_EXPERIMENT} create-definition ${LARCV_DTAG} "Dataset.Tag ${LARCV_DTAG}"
    fi
else
    echo "SAVE_LARCV=0: skipping larcv copy/declare"
fi

echo -e "Done"
echo "$0 done"
