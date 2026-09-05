#!/usr/bin/env bash
# HU-2708 vector-only ablation arm — second gateway instance (read-only).
#
# Starts a SEPARATE tdai-gateway on :8421 with the v3 read path and the
# embedding-only recall strategy (no FTS/BM25, no Arm A gist digest, no
# drill-down) against the SAME data dir + embedding service as production.
# Capture/extraction are disabled so the instance never writes — it is a
# recall-only mirror for the ablation, not a second production gateway.
#
# The derived config is written to $PAPERCLIP_SCRATCH_DIR (or a mktemp dir)
# from the LIVE config at start time, so no secrets ever land in the repo.
#
# Usage:
#   launch_vectoronly_gateway.sh start   # idempotent; prints PID + waits for /health
#   launch_vectoronly_gateway.sh stop    # kills the instance started above
set -euo pipefail

LIVE_CONFIG="${LIVE_CONFIG:-/opt/tencentdb-memory/.config/tdai-gateway.yaml}"
MEMORY_CORE="${MEMORY_CORE:-/opt/tencentdb-memory/MemoryCore}"
PORT=8421
HEALTH="http://127.0.0.1:${PORT}/health"

SCRATCH="${PAPERCLIP_SCRATCH_DIR:-$(mktemp -d)}"
mkdir -p "$SCRATCH"
CFG="$SCRATCH/tdai-gateway-vectoronly.yaml"
PIDFILE="$SCRATCH/gateway-vectoronly.pid"
LOGFILE="$SCRATCH/gateway-vectoronly.log"

derive_config() {
  python3 - "$LIVE_CONFIG" "$CFG" <<'PY'
import re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()

subs = [
    # (pattern, replacement, expected count)
    (r"(?m)^  port: 8420$", "  port: 8421", 1),
    (r"(?m)^  capture:\n    enabled: true$", "  capture:\n    enabled: false", 1),
    (r"(?m)^  extraction:\n    enabled: true$", "  extraction:\n    enabled: false", 1),
    (r"(?m)^  recall:\n    enabled: true$",
     "  recall:\n    enabled: true\n    readPath: v3\n    strategy: embedding", 1),
]
for pat, rep, want in subs:
    text, n = re.subn(pat, rep, text)
    if n != want:
        sys.exit(f"config surgery failed for pattern {pat!r}: {n} matches (wanted {want})")
open(dst, "w", encoding="utf-8").write(text)
print(f"derived config: {dst}")
PY
}

wait_health() {
  for _ in $(seq 1 45); do
    if curl -sf -m 2 "$HEALTH" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

case "${1:-}" in
  start)
    if curl -sf -m 2 "$HEALTH" >/dev/null 2>&1; then
      echo "already running: $HEALTH (pid $(cat "$PIDFILE" 2>/dev/null || echo '?'))"
      exit 0
    fi
    derive_config
    ( cd "$MEMORY_CORE" && \
      TDAI_GATEWAY_CONFIG="$CFG" setsid nohup node --import tsx src/gateway/server.ts \
        >"$LOGFILE" 2>&1 </dev/null & echo $! >"$PIDFILE" )
    if wait_health; then
      echo "vector-only gateway up: $HEALTH pid=$(cat "$PIDFILE") log=$LOGFILE"
    else
      echo "gateway failed health check — log tail:" >&2
      tail -20 "$LOGFILE" >&2
      exit 1
    fi
    ;;
  stop)
    if [[ -f "$PIDFILE" ]]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"
      echo "stopped"
    else
      echo "no pidfile at $PIDFILE" >&2
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 {start|stop}" >&2
    exit 2
    ;;
esac
