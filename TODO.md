# TODO

- [x] Integrate recovery campaign feature.
Workflow: Trigger when no slices are left. Check the status of the last slice. When it is completed, run the recovery script.
Inside the script:
1. samweb list-definition-files $INPUT_DATASET | head -n 1 # first input file
2. samweb file-lineage $input_file # array of output files
3. Get the array of output dataset definitions
for i in $outputs; do samweb get-metadata $i | grep 'Dataset.Tag' | awk -F ': ' '{print $2}'; done
Save output names in a txt file.
4. check how many files there are in the output datasets using list-definition-files --summary.
5. If output/input > 0.98, do not setup recovery.
6. Else: create recovery dataset, prestage input if data campaign. Campaign type can be inferred from the name of the
input file. If it has 'data_EventBuilder' in its name then it is a data campaign.
7. change the input dataset name in POMS, set cs_splits to 0. Submit a slice.
- [ ] Add File Cleanup feature.
1. get the output defnames from the txt file saved in step 3 of the recovery campaign.
2. rest is already implemented.
- [ ] The cron script should be a single file instead of multiple command stringed together.
