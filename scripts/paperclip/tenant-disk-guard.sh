#!/bin/bash
# tenant-disk-guard.sh — daily disk hygiene for non-paperclip tenants (HU-2781 AC#1/AC#3).
# Bounds the co-tenant churn that sawtoothed the root volume below the 15% floor:
#   1. /tmp sweep: top-level entries older than 7 days (excludes systemd/snap private dirs).
#   2. Docker: dangling-image + build-cache prune (running containers/images untouched).
#   3. Git: eq-vaults + inner data/dynamic-reaction — gc.auto disabled (auto-gc was
#      surprise-packing multi-GB media), then budgeted gc with 30d prune window.
#   4. Hermes checkpoints (/root/.hermes/checkpoints/store): drop refs/hermes older
#      than 30d, gc with the same window (recent undo state preserved).
# Evidence: summary line appended to /var/log/paperclip-retention.log for the
# 48h root-free-floor watch (AC#4).
set -u
LOCK=/run/tenant-disk-guard.lock
exec 9>"$LOCK"
flock -n 9 || { echo "$(date -u +%FT%TZ) tenant_guard: skipped, previous run still active"; exit 0; }

freed_tmp=0; freed_docker=0
free_gb() { df -BG --output=avail / | tail -1 | tr -dc '0-9'; }

EMERGENCY_ONLY=0
[ "${1:-}" = "--emergency-only" ] && EMERGENCY_ONLY=1

echo "$(date -u +%FT%TZ) tenant_guard: start free=$(free_gb)G"

# --- 1. /tmp sweep (>7d) -----------------------------------------------------
mapfile -t stale < <(find /tmp -mindepth 1 -maxdepth 1 \
  ! -name 'systemd-private-*' ! -name 'snap-private-tmp' ! -name 'snap-tmpfs' \
  ! -name '.X11-unix' ! -name '.ICE-unix' ! -name '.font-unix' ! -name '.XIM-unix' \
  ! -name 'tmux-*' \
  -mtime +7 -printf '%p\n' 2>/dev/null)
for p in "${stale[@]:-}"; do
  [ -n "$p" ] || continue
  # never delete entries that are or contain live sockets
  [ -S "$p" ] && continue
  [ -n "$(find "$p" -type s -print -quit 2>/dev/null)" ] && continue
  sz=$(du -sm "$p" 2>/dev/null | cut -f1); sz=${sz:-0}
  if rm -rf -- "$p" 2>/dev/null; then
    freed_tmp=$((freed_tmp + sz))
    echo "tenant_guard: tmp removed ${sz}MB $p"
  fi
done

# --- 1b. Emergency pass when below the 15% floor (18G/119G) -------------------
FLOOR_GB="${GUARD_FLOOR_GB:-18}"
if [ "$(free_gb)" -lt "$FLOOR_GB" ]; then
  echo "tenant_guard: EMERGENCY pass, free=$(free_gb)G < ${FLOOR_GB}G floor"
  mapfile -t stale2 < <(find /tmp -mindepth 1 -maxdepth 1 \
    ! -name 'systemd-private-*' ! -name 'snap-private-tmp' ! -name 'snap-tmpfs' \
    ! -name '.X11-unix' ! -name '.ICE-unix' ! -name '.font-unix' ! -name '.XIM-unix' \
    ! -name 'tmux-*' ! -name 'paperclip-run-*' \
    -mtime +2 -printf '%p\n' 2>/dev/null)
  for p in "${stale2[@]:-}"; do
    [ -n "$p" ] || continue
    [ -S "$p" ] && continue
    [ -n "$(find "$p" -type s -print -quit 2>/dev/null)" ] && continue
    sz=$(du -sm "$p" 2>/dev/null | cut -f1); sz=${sz:-0}
    if rm -rf -- "$p" 2>/dev/null; then
      freed_tmp=$((freed_tmp + sz))
      echo "tenant_guard: emergency tmp removed ${sz}MB $p"
    fi
  done
  echo "$(date -u +%FT%TZ) tenant_guard EMERGENCY free_was_below_floor=${FLOOR_GB}G" >> /var/log/paperclip-retention.log
fi

[ "$EMERGENCY_ONLY" -eq 1 ] && { echo "$(date -u +%FT%TZ) tenant_guard: emergency-only pass done free=$(free_gb)G"; exit 0; }

# --- 2. Docker dangling prune -------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  out=$(docker image prune -f 2>&1 | grep -oP 'Total reclaimed space: \K.*' | head -1)
  echo "tenant_guard: docker image prune reclaimed ${out:-0B}"
  out=$(docker builder prune -f 2>&1 | grep -oP 'Total reclaimed space: \K.*' | head -1)
  echo "tenant_guard: docker builder prune reclaimed ${out:-0B}"
fi

# --- 3. Git repos: disable auto-gc, budgeted gc ------------------------------
for repo in /root/repos/eq-vaults /root/repos/eq-vaults/data/dynamic-reaction; do
  [ -d "$repo/.git" ] || continue
  git -C "$repo" config gc.auto 0
  before=$(du -sm "$repo/.git" 2>/dev/null | cut -f1)
  if git -C "$repo" reflog expire --expire=30.days.ago --all 2>/dev/null \
     && git -C "$repo" gc --prune=30.days.ago --quiet 2>/dev/null; then
    after=$(du -sm "$repo/.git" 2>/dev/null | cut -f1)
    echo "tenant_guard: git gc $repo .git ${before}MB -> ${after}MB"
  else
    echo "tenant_guard: git gc $repo skipped or failed (repo busy?)"
  fi
done

# --- 4. Hermes checkpoints store (bare git repo) ------------------------------
CK=/root/.hermes/checkpoints/store
if git -C "$CK" rev-parse --git-dir >/dev/null 2>&1; then
  before=$(du -sm "$CK" 2>/dev/null | cut -f1)
  cutoff=$(date -d '30 days ago' +%s)
  git -C "$CK" for-each-ref --format='%(refname) %(committerdate:unix)' refs/hermes 2>/dev/null \
    | while read -r ref ts; do
        [ -n "$ts" ] && [ "$ts" -lt "$cutoff" ] && git -C "$CK" update-ref -d "$ref"
      done
  git -C "$CK" reflog expire --expire=30.days.ago --all 2>/dev/null || true
  git -C "$CK" gc --prune=30.days.ago --quiet 2>/dev/null || true
  after=$(du -sm "$CK" 2>/dev/null | cut -f1)
  echo "tenant_guard: checkpoints ${before}MB -> ${after}MB (refs kept: $(git -C "$CK" for-each-ref refs/hermes 2>/dev/null | wc -l))"
fi

echo "$(date -u +%FT%TZ) tenant_guard: done freed_tmp=${freed_tmp}MB free_now=$(free_gb)G"
# Evidence line for the retention log (AC#4 watch).
echo "$(date -u +%FT%TZ) tenant_guard freed=${freed_tmp}MB free=$(free_gb)G" >> /var/log/paperclip-retention.log
