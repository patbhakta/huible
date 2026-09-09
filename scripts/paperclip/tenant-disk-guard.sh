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
to_mb() { printf '%s' "${1:-0B}" | awk '{if ($0 ~ /GB/) printf "%d", $1*1024; else if ($0 ~ /MB/) printf "%d", $1; else if ($0 ~ /kB/) printf "%d", $1/1024; else printf "%d", $1/1048576}'; }

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

# --- 2. Docker dangling prune (runs in EVERY pass, not just the daily one) ----
# HU-2781 churn source: huible-app is rebuilt on each deploy (~330-660MB of
# layers per build; 3 builds seen within one hour on 2026-09-09). Waiting for
# the 04:15 daily pass let ~1G/h accumulate overnight -> the -19G sawtooth.
# keep-storage=2G keeps recent cache so rebuilds stay incremental while capping
# total build-cache growth between runs.
docker_bounded_prune() {
  command -v docker >/dev/null 2>&1 || return 0
  local img cache flag
  if docker builder prune --help 2>&1 | grep -q reserved-space; then flag=--reserved-space=2G; else flag=--keep-storage=2G; fi
  img=$(docker image prune -f 2>&1 | grep -oP '(Total reclaimed space:|Total:)\s*\K.*' | head -1)
  cache=$(docker builder prune $flag -f 2>&1 | grep -oP '(Total reclaimed space:|Total:)\s*\K.*' | head -1)
  freed_docker=$(( freed_docker + $(to_mb "${img:-0B}") + $(to_mb "${cache:-0B}") ))
  echo "tenant_guard: docker prune reclaimed img=${img:-0B} cache=${cache:-0B}"
}

[ "$EMERGENCY_ONLY" -eq 1 ] && {
  docker_bounded_prune
  echo "$(date -u +%FT%TZ) tenant_guard: emergency-only pass done freed_docker=${freed_docker}MB free=$(free_gb)G"
  exit 0
}

# --- 1c. /tmp size budget (AC#3) ----------------------------------------------
# Overnight agents RECREATE scratch dirs (rag, deepdoc-models, ragflow) nightly,
# so their top-level mtime resets and they never age past the 7d sweep. When
# /tmp exceeds the budget, drop scratch entries older than 24h (today's fresh
# scratch stays for still-running work).
tmp_budget_mb="${TMP_BUDGET_MB:-3072}"
tmp_now=$(du -sm /tmp 2>/dev/null | cut -f1)
if [ "${tmp_now:-0}" -gt "$tmp_budget_mb" ]; then
  echo "tenant_guard: /tmp ${tmp_now}MB over ${tmp_budget_mb}MB budget, sweeping >24h scratch"
  mapfile -t scratch < <(find /tmp -mindepth 1 -maxdepth 1 \
    ! -name 'systemd-private-*' ! -name 'snap-private-tmp' ! -name 'snap-tmpfs' \
    ! -name '.X11-unix' ! -name '.ICE-unix' ! -name '.font-unix' ! -name '.XIM-unix' \
    ! -name 'tmux-*' ! -name 'paperclip-run-*' \
    -mmin +1440 -printf '%p\n' 2>/dev/null)
  for p in "${scratch[@]:-}"; do
    [ -n "$p" ] || continue
    [ "$(du -sm /tmp 2>/dev/null | cut -f1)" -le "$tmp_budget_mb" ] && break
    [ -S "$p" ] && continue
    [ -n "$(find "$p" -type s -print -quit 2>/dev/null)" ] && continue
    sz=$(du -sm "$p" 2>/dev/null | cut -f1); sz=${sz:-0}
    if rm -rf -- "$p" 2>/dev/null; then
      freed_tmp=$((freed_tmp + sz))
      echo "tenant_guard: tmp budget removed ${sz}MB $p"
    fi
  done
fi

docker_bounded_prune

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

echo "$(date -u +%FT%TZ) tenant_guard: done freed_tmp=${freed_tmp}MB freed_docker=${freed_docker}MB free_now=$(free_gb)G"
# Evidence line for the retention log (AC#4 watch).
echo "$(date -u +%FT%TZ) tenant_guard freed=${freed_tmp}MB docker=${freed_docker}MB free=$(free_gb)G" >> /var/log/paperclip-retention.log
