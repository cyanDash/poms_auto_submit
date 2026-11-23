#!/bin/bash
# Runs one Output Definition's SBN migration on this GPVM, inside the screen
# session scripts/migration.py starts. See docs/adr/0027.
# Usage: run_migration_job.sh <definition> <log_file>

definition="$1"
log="$2"
mirror="${MIRROR_DIR:-/exp/sbnd/app/users/sbndpro/sbnd_to_sbn_mirror}"
uid=$(id -u)

# No claim yet: a failure here leaves the definition free for another machine.
htgettoken --credkey=sbndpro/managedtokens/fifeutilgpvm01.fnal.gov \
  -a htvaultprod.fnal.gov -i sbnd -r production --nooidc --nokerberos --nossh \
  --vaulttokenfile="/tmp/vt_u${uid}-sbnd_production" -o "/tmp/bt_u${uid}_production" \
  < /dev/null > "${log}.token" 2>&1 || exit 2

# A symlink is created atomically and records which machine owns the run.
ln -s "$(hostname -f)" "${log}.claim" 2> /dev/null || exit 3

setsid bash "${mirror}/refresh_token_loop.sh" >> "${log}.token" 2>&1 &
refresh_pid=$!
trap 'kill -- "-${refresh_pid}" 2> /dev/null' EXIT

bash "${mirror}/run_migration_container.sh" "$definition" "$log"
