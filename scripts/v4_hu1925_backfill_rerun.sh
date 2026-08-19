#!/usr/bin/env bash
# HU-1925: re-run hermes-jarvis-main gist backfill after the queue drains.
# The queue's first pass hit rc=143 mid-block-5 (block 5 had generated 10078
# chars finishReason=stop but was killed before save). Blocks 5,7,8,9,10,11
# of 12 complete blocks (518 L0 rows) remain. Appends results to the shared
# backfill log + done file.
set -u
cd /opt/tencentdb-memory/MemoryCore || exit 1
LOG=/root/repos/huible/logs/hu1925-backfill.log
DONE=/root/repos/huible/logs/hu1925-backfill.done
export TDAI_GATEWAY_CONFIG=/opt/tencentdb-memory/.config/tdai-gateway.yaml

# wait for the queue script and any in-flight backfill to finish
while pgrep -f "v4_hu1925_backfill_queue.sh" >/dev/null 2>&1; do sleep 30; done
while pgrep -f "backfill-gists-hu-1925/backfill.ts" >/dev/null 2>&1; do sleep 30; done

echo "[rerun] queue drained; hermes-jarvis-main backfill rerun start ($(date -u +%FT%TZ))" >> "$LOG"
npx tsx scripts/backfill-gists-hu-1925/backfill.ts --session hermes-jarvis-main >> "$LOG" 2>&1
rc=$?
blocks=$(python3 -c "import json;d=json.load(open('/root/.memory-tencentdb/memory-tdai/gists/hermes-jarvis-main.json'));print(len(d))" 2>/dev/null || echo 0)
echo "hermes-jarvis-main rerun rc=$rc blocks=$blocks" >> "$DONE"
echo "[rerun] done rc=$rc blocks=$blocks ($(date -u +%FT%TZ))" >> "$LOG"
