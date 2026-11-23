#!/bin/bash
# One-shot check, run from sbndpro's crontab, of everything the SBN migration
# needs from cron. Starts no screen and no migration, and changes nothing in
# SAM. Writes one log; its last line is the verdict. See docs/adr/0027.

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="${LOG:-$HERE/../logs/migration_cron_probe_$(date +%Y%m%d_%H%M%S).log}"
KEYTAB="${KEYTAB:-$HOME/$(id -u).keytab}"
MIRROR=/exp/sbnd/app/users/sbndpro/sbnd_to_sbn_mirror
mkdir -p "$(dirname "$LOG")"

# Runs on each GPVM. The test token goes to its own file, so a running
# migration's token is never touched.
read -r -d '' REMOTE <<EOF
echo "user \$(id -un) on \$(hostname -f)"
bad=0
for tool in screen htgettoken; do
  command -v "\$tool" > /dev/null && echo "  ok      \$tool" || { echo "  MISSING \$tool"; bad=1; }
done
for f in /cvmfs/oasis.opensciencegrid.org/mis/apptainer/current/bin/apptainer \
  $MIRROR/migrate_definition.sh $MIRROR/run_migration_container.sh $MIRROR/refresh_token_loop.sh \
  "\$HOME/setup_SBND2026A.sh" "/tmp/vt_u\$(id -u)-sbnd_production"; do
  [ -r "\$f" ] && echo "  ok      \$f" || { echo "  MISSING \$f"; bad=1; }
done
[ -w $MIRROR ] && echo "  ok      $MIRROR writable" || { echo "  MISSING write access to $MIRROR"; bad=1; }
probe_token=/tmp/bt_u\$(id -u)_migration_probe_\$\$
if htgettoken --credkey=sbndpro/managedtokens/fifeutilgpvm01.fnal.gov \
    -a htvaultprod.fnal.gov -i sbnd -r production --nooidc --nokerberos --nossh \
    --vaulttokenfile=/tmp/vt_u\$(id -u)-sbnd_production -o "\$probe_token" < /dev/null > /dev/null 2>&1; then
  echo "  ok      token minted without a login"
else
  echo "  MISSING token could not be minted"; bad=1
fi
rm -f "\$probe_token"
klist -s 2> /dev/null && echo "  note    a Kerberos ticket exists on this machine" || echo "  ok      no Kerberos ticket left here"
exit \$bad
EOF

{
  echo "===== $(date '+%F %T %Z') on $(hostname -f) as $(id -un)"
  echo "PATH=$PATH"

  export KRB5CCNAME="FILE:$(mktemp -u "/tmp/krb5cc_$(id -u)_migration_probe_XXXXXX")"
  trap 'kdestroy -q 2>/dev/null' EXIT
  PRINC="$(klist -k "$KEYTAB" 2>/dev/null | awk 'NR>3 {print $2; exit}')"
  if [ -z "$PRINC" ] || ! kinit -k -t "$KEYTAB" "$PRINC"; then
    echo "VERDICT: FAIL, kinit with keytab $KEYTAB failed"
    exit 1
  fi
  echo "ok keytab login as $PRINC"

  usable=0
  for n in 1 2 3 4 5 6 7; do
    host="sbndgpvm0$n.fnal.gov"
    echo "--- $host"
    timeout 90 ssh -o BatchMode=yes -o ConnectTimeout=10 -o GSSAPIDelegateCredentials=no \
      "$host" "$REMOTE" < /dev/null 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
      usable=$((usable + 1))
      echo "  USABLE"
    else
      echo "  NOT USABLE (rc=$rc)"
    fi
  done

  if [ "$usable" -gt 0 ]; then
    echo "VERDICT: PASS, $usable of 7 machines usable"
  else
    echo "VERDICT: FAIL, 0 of 7 machines usable"
    exit 1
  fi
} >> "$LOG" 2>&1
