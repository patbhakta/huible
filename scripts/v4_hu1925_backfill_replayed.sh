#!/usr/bin/env bash
# HU-1925: generate gists for 20260818_062408_4a0e0c61 after L0 replay closed
# the rate-limit sync gap (105 hermes turns -> 50 L0 rows via v4_hu1925_l0_replay.py).
# Block 0 (rows 1-40) is complete; backfill uses the same path as the four
# legacy sessions (maxTokens now 32k in gateway config).
set -u
cd /opt/tencentdb-memory/MemoryCore || exit 1
LOG=/root/repos/huible/logs/hu1925-backfill.log
DONE=/root/repos/huible/logs/hu1925-backfill.done
export TDAI_GATEWAY_CONFIG=/opt/tencentdb-memory/.config/tdai-gateway.yaml

while pgrep -f "backfill-gists-hu-1925/backfill.ts" >/dev/null 2>&1; do sleep 30; done

echo "[replay-backfill] 4a0e0c61 start ($(date -u +%FT%TZ))" >> "$LOG"
npx tsx scripts/backfill-gists-hu-1925/backfill.ts --session 20260818_062408_4a0e0c61 >> "$LOG" 2>&1
rc=$?
blocks=$(python3 -c "import json;print(len(json.load(open('/root/.memory-tencentdb/memory-tdai/gists/20260818_062408_4a0e0c61.json'))))" 2>/dev/null || echo 0)
echo "20260818_062408_4a0e0c61 replay-backfill rc=$rc blocks=$blocks" >> "$DONE"
echo "[replay-backfill] 4a0e0c61 done rc=$rc blocks=$blocks ($(date -u +%FT%TZ))" >> "$LOG"
