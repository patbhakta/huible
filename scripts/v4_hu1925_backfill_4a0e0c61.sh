#!/usr/bin/env bash
# HU-1925: generate gists for 20260818_062408_4a0e0c61 (50 L0 rows after the
# 20:28Z replay; prior gist run was killed at 20:34:33 when its heartbeat ended).
# Detached-safe: run under setsid/nohup so heartbeat teardown cannot kill it.
set -u
cd /opt/tencentdb-memory/MemoryCore || exit 1
LOG=/root/repos/huible/logs/hu1925-backfill.log
DONE=/root/repos/huible/logs/hu1925-backfill.done
SNAP=/root/repos/huible/logs/hu1925-gist-final-snapshot.txt
SID=20260818_062408_4a0e0c61
export TDAI_GATEWAY_CONFIG=/opt/tencentdb-memory/.config/tdai-gateway.yaml

while pgrep -f "backfill-gists-hu-1925/backfill.ts" >/dev/null 2>&1; do sleep 30; done

echo "[4a0e0c61] gist backfill start ($(date -u +%FT%TZ))" >> "$LOG"
npx tsx scripts/backfill-gists-hu-1925/backfill.ts --session "$SID" >> "$LOG" 2>&1
rc=$?
blocks=$(python3 -c "import json;print(len(json.load(open('/root/.memory-tencentdb/memory-tdai/gists/$SID.json'))))" 2>/dev/null || echo 0)
echo "$SID rerun rc=$rc blocks=$blocks ($(date -u +%FT%TZ))" >> "$DONE"
echo "[4a0e0c61] gist backfill done rc=$rc blocks=$blocks ($(date -u +%FT%TZ))" >> "$LOG"

{
  echo "HU-1925 final gist-state snapshot ($(date -u +%FT%TZ))"
  python3 - <<'PY'
import json, glob
for f in sorted(glob.glob('/root/.memory-tencentdb/memory-tdai/gists/*.json')):
    d = json.load(open(f))
    blocks = d if isinstance(d, list) else [d]
    flagged = [b.get('blockIndex', i) for i, b in enumerate(blocks) if b.get('contractMiss')]
    degenerate = [b.get('blockIndex', i) for i, b in enumerate(blocks) if not (b.get('arc') and b.get('enteredRecord'))]
    print(f"{f.split('/')[-1]}: blocks={len(blocks)} flagged_cm={flagged or 'none'} degenerate={degenerate or 'none'}")
PY
} > "$SNAP" 2>&1
echo "[4a0e0c61] snapshot refreshed at $(date -u +%FT%TZ))" >> "$LOG"
