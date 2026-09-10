#!/bin/bash
# tenant-disk-watch.sh — fine-grained per-tenant disk attribution (HU-2781 AC#1).
# Runs every 15 min in the 22:00-03:00 churn window (see cron root entry).
# The hourly paperclip-retention.log lines showed a recurring ~19G dip between
# 23:15Z and 00:15Z that recovered hours later, but hourly granularity could not
# attribute it. This watcher snapshots the known big tenants so the next dip is
# attributed with hard evidence. Read-only; never deletes.
set -u
ts=$(date -u +%FT%TZ)
free_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
echo "== $ts free=${free_gb}G"
targets=(
  /tmp
  /var/lib/docker
  /var/lib/containerd
  /backups
  /root/.hermes
  /root/.local/share/opencode
  /root/repos/eq-vaults/.git
  /root/repos/eq-vaults/data/dynamic-reaction/.git
  /root/repos/brain
  /var/log
)
for t in "${targets[@]}"; do
  [ -e "$t" ] || continue
  printf '%s %s\n' "$(du -sm "$t" 2>/dev/null | cut -f1)" "$t"
done
# flag any single /tmp entry or loose-object dir > 500M for fast attribution
find /tmp -mindepth 1 -maxdepth 1 -type d -exec du -sm {} + 2>/dev/null | awk '$1 > 500 {print "TMPBIG " $0}'
for gitdir in /root/repos/eq-vaults/data/dynamic-reaction/.git/objects /root/repos/eq-vaults/.git/objects; do
  [ -d "$gitdir" ] || continue
  loose=$(find "$gitdir" -type f -path '*/??/*' 2>/dev/null | wc -l)
  [ "$loose" -gt 100 ] && echo "GITLOOSE $gitdir loose=$loose"
done
# --- bounded remediation trigger (HU-2781 AC#4) --------------------------------
# Overnight deploy bursts pile 2-4G within minutes; waiting for the hourly
# :45 emergency cron let free dip to 15G (Sep 10 00:45-05:45Z, 6 EMERGENCY
# passes). When free approaches the 18G floor, run the guard's bounded prune
# NOW so dips are capped within 15 min instead of 60. Guard is flock-protected.
TRIGGER_GB="${GUARD_TRIGGER_GB:-20}"
if [ "$free_gb" -lt "$TRIGGER_GB" ]; then
  echo "GUARDTRIGGER free=${free_gb}G < ${TRIGGER_GB}G, invoking tenant-disk-guard --emergency-only"
  /root/scripts/tenant-disk-guard.sh --emergency-only
fi
