#!/usr/bin/env bash
# HU-1925: sequential gist backfill queue for all gist-eligible sessions.
# Waits for any in-flight hermes-jarvis-main backfill, then processes the
# legacy sessions flagged by the watch probe. Writes logs/hu1925-backfill.done
# with a per-session summary when the whole queue drains.
set -u
cd /opt/tencentdb-memory/MemoryCore || exit 1
LOG=/root/repos/huible/logs/hu1925-backfill.log
DONE=/root/repos/huible/logs/hu1925-backfill.done
export TDAI_GATEWAY_CONFIG=/opt/tencentdb-memory/.config/tdai-gateway.yaml

# wait for an already-running backfill (hermes-jarvis-main) to finish
while pgrep -f "backfill-gists-hu-1925/backfill.ts" >/dev/null 2>&1; do sleep 20; done

: > "$DONE"
for s in hermes-jarvis-main \
         20260808_042204_295b6f82 \
         20260809_060541_56aef239 \
         20260810_040559_5985b592 \
         20260817_044013_27138159; do
  echo "[queue] backfill start: $s ($(date -u +%FT%TZ))" >> "$LOG"
  npx tsx scripts/backfill-gists-hu-1925/backfill.ts --session "$s" >> "$LOG" 2>&1
  rc=$?
  blocks=$(python3 -c "import json,sys;d=json.load(open('/root/.memory-tencentdb/memory-tdai/gists/$s.json'));print(len(d))" 2>/dev/null || echo 0)
  echo "$s rc=$rc blocks=$blocks" >> "$DONE"
done
echo "[queue] all done $(date -u +%FT%TZ)" >> "$LOG"
