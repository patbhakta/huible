#!/usr/bin/env bash
# HU-1743: one-shot DNS watcher — auto-activate huible.bhakta.us the moment
# the board-approved A record lands, without waiting for the confirmation
# card click or the next TL heartbeat.
#
# Board decision already recorded: card 55a6cf56 (accepted 2026-08-18) approved
# huible.bhakta.us as HUIBLE_DOMAIN. The pending card 1402f3f5 only confirms
# record creation; DNS existence is a fact this watcher can detect directly.
#
# Behaviour:
#   - Poll every POLL_SEC via dig @1.1.1.1 (local resolver SERVFAILs on zone).
#   - Fire ONLY on exact A match 208.84.102.245 (grey-cloud expected).
#   - On fire: run scripts/activate_huible_domain.sh huible.bhakta.us
#     (idempotent, 7/7 pre-check PASS) and exit.
#   - Self-disarm after MAX_HOURS (exit 124) or if activation already done
#     (valid origin cert already served).
# All output tees to logs/watch-huible-dns-<ts>.log (symlink ...-latest.log).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

DOMAIN="huible.bhakta.us"
EXPECTED_IP="208.84.102.245"
POLL_SEC="${POLL_SEC:-180}"
MAX_HOURS="${MAX_HOURS:-72}"
LOG_DIR="logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/watch-huible-dns-$(date -u +%Y%m%dT%H%M%SZ).log"
ln -sfn "$(basename "$LOG")" "$LOG_DIR/watch-huible-dns-latest.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

already_active() { # 0 when the origin already serves a public cert for DOMAIN
  local issuer
  issuer="$(timeout 10 openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer 2>/dev/null)"
  # internal/placeholder CA means not yet activated via ACME
  [[ -n "$issuer" && "$issuer" != *"Caddy Local Authority"* && "$issuer" != *"internal"* ]]
}

dns_a() { timeout 8 dig +short +time=5 +tries=1 @1.1.1.1 "$DOMAIN" A 2>/dev/null \
  | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1; }

log "watcher start: domain=$DOMAIN expected=$EXPECTED_IP poll=${POLL_SEC}s max=${MAX_HOURS}h pid=$$"

if already_active; then
  log "origin cert already active for $DOMAIN — activation previously completed, watcher disarming."
  exit 0
fi

deadline=$(( $(date +%s) + MAX_HOURS * 3600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  a="$(dns_a)"
  if [ "$a" = "$EXPECTED_IP" ]; then
    log "A record LIVE: $DOMAIN -> $a — firing activation."
    bash scripts/activate_huible_domain.sh "$DOMAIN" 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    log "activation finished rc=$rc — watcher exiting (one-shot)."
    exit "$rc"
  elif [ -n "$a" ]; then
    log "A mismatch: got $a, want $EXPECTED_IP (proxied/orange-cloud?) — continuing to wait."
  fi
  sleep "$POLL_SEC"
done

log "deadline (${MAX_HOURS}h) reached without DNS — watcher self-disarming (exit 124). Card/manual path unaffected."
exit 124
