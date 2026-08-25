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

# WATCH_DOMAIN lets the same watcher cover the de-facto huible.com path the
# founder DM'd (HU-1743 comment 000f1716) — default stays the board-approved
# bhakta.us zone so the running instance and runbook log names are unchanged.
DOMAIN="${WATCH_DOMAIN:-huible.bhakta.us}"
EXPECTED_IP="208.84.102.245"
POLL_SEC="${POLL_SEC:-180}"
MAX_HOURS="${MAX_HOURS:-72}"
LOG_DIR="logs"; mkdir -p "$LOG_DIR"
if [ "$DOMAIN" = "huible.bhakta.us" ]; then
  LOG="$LOG_DIR/watch-huible-dns-$(date -u +%Y%m%dT%H%M%SZ).log"
  ln -sfn "$(basename "$LOG")" "$LOG_DIR/watch-huible-dns-latest.log"
else
  slug="$(echo "$DOMAIN" | tr '.' '-')"
  LOG="$LOG_DIR/watch-$slug-$(date -u +%Y%m%dT%H%M%SZ).log"
  ln -sfn "$(basename "$LOG")" "$LOG_DIR/watch-$slug-latest.log"
fi

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"; }

# Best-effort WhatsApp ping to Pat (Hermes bridge) so fire/deadline events are
# human-visible immediately instead of waiting for the next TL heartbeat.
# Never fatal: watcher behaviour is unchanged if delivery fails.
NOTIFY_CMD="/root/.local/bin/hermes"
notify() { # notify "<subject>" "<body>"
  local subj="$1" body="$2"
  if [[ -x "$NOTIFY_CMD" ]]; then
    if timeout 45 "$NOTIFY_CMD" send --to "whatsapp:Pat Bhakta (dm)" -s "$subj" "$body" >/dev/null 2>&1; then
      log "notify: sent '$subj'"
    else
      log "notify: hermes send FAILED for '$subj' (non-fatal)"
    fi
  else
    log "notify: hermes not found at $NOTIFY_CMD (non-fatal)"
  fi
}

cert_active() { # 0 when the origin already serves a public ACME cert for $1
  local d="$1" issuer
  issuer="$(timeout 10 openssl s_client -connect 127.0.0.1:443 -servername "$d" </dev/null 2>/dev/null \
    | openssl x509 -noout -issuer 2>/dev/null)"
  # internal/placeholder CA means not yet activated via ACME
  [[ -n "$issuer" && "$issuer" != *"Caddy Local Authority"* && "$issuer" != *"internal"* ]]
}

already_active() { cert_active "$DOMAIN"; }

# Dual-watcher guard (HU-1743): both huible.bhakta.us and huible.com watchers
# can be armed at once while the launch-domain card is pending. The activation
# script overwrites HUIBLE_DOMAIN last-writer-wins, so a late second fire would
# silently flip the production domain post-launch. Before firing, stand down if
# a DIFFERENT domain has already claimed production (env claim or live cert).
KNOWN_DOMAINS="huible.bhakta.us huible.com"
ENV_FAILOVER=".env.failover"
conflicting_domain() { # echoes the already-claimed domain (≠ DOMAIN), rc 1 if none
  local claimed d
  claimed="$(grep -m1 '^HUIBLE_DOMAIN=' "$ENV_FAILOVER" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
  # localhost/127.0.0.1 are pre-activation placeholders, not real claims
  case "$claimed" in ""|localhost|127.0.0.1) claimed="";; esac
  if [ -n "$claimed" ] && [ "$claimed" != "$DOMAIN" ]; then
    echo "$claimed"; return 0
  fi
  for d in $KNOWN_DOMAINS; do
    [ "$d" = "$DOMAIN" ] && continue
    if cert_active "$d"; then echo "$d"; return 0; fi
  done
  return 1
}

dns_a() { timeout 8 dig +short +time=5 +tries=1 @1.1.1.1 "$DOMAIN" A 2>/dev/null \
  | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1; }

log "watcher start: domain=$DOMAIN expected=$EXPECTED_IP poll=${POLL_SEC}s max=${MAX_HOURS}h pid=$$"

if already_active; then
  log "origin cert already active for $DOMAIN — activation previously completed, watcher disarming."
  exit 0
fi
if other="$(conflicting_domain)"; then
  log "CONFLICT: production already claimed by $other (env/cert) — this watcher ($DOMAIN) standing down WITHOUT firing."
  notify "[HU-1743] watcher conflict: $other already active" "Watcher for $DOMAIN stood down without activating: production already claimed by $other. Manual/attended decision required to switch domains — nothing was changed."
  exit 0
fi

deadline=$(( $(date +%s) + MAX_HOURS * 3600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  a="$(dns_a)"
  if [ "$a" = "$EXPECTED_IP" ]; then
    if other="$(conflicting_domain)"; then
      log "A record LIVE for $DOMAIN but production already claimed by $other — NOT firing activation (attended switch required)."
      notify "[HU-1743] $DOMAIN DNS live but $other already active" "A record for $DOMAIN is live, but production was already activated on $other. No change made. If the launch domain should be $DOMAIN, Tech Lead must run the switch manually."
      exit 0
    fi
    log "A record LIVE: $DOMAIN -> $a — firing activation."
    bash scripts/activate_huible_domain.sh "$DOMAIN" 2>&1 | tee -a "$LOG"
    rc=${PIPESTATUS[0]}
    if [ "$rc" -eq 0 ]; then
      notify "[HU-1743] $DOMAIN ACTIVATED" "DNS A record is live and activation succeeded (rc=0). Public TLS + domain now serving; Tech-Lead heartbeat will verify and close out HU-1743."
    else
      notify "[HU-1743] ACTIVATION FAILED rc=$rc" "DNS was live but activate_huible_domain.sh failed (rc=$rc). See logs/watch-huible-dns-latest.log on the prod host — needs Tech-Lead attention."
    fi
    log "activation finished rc=$rc — watcher exiting (one-shot)."
    exit "$rc"
  elif [ -n "$a" ]; then
    log "A mismatch: got $a, want $EXPECTED_IP (proxied/orange-cloud?) — continuing to wait."
  fi
  sleep "$POLL_SEC"
done

notify "[HU-1743] DNS watcher disarmed" "${MAX_HOURS}h window closed with no A record for $DOMAIN. Card 1402f3f5 / manual activation path unaffected; HU-1743 still waiting on the DNS record -> $EXPECTED_IP."
log "deadline (${MAX_HOURS}h) reached without DNS — watcher self-disarming (exit 124). Card/manual path unaffected."
exit 124
