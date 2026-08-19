#!/usr/bin/env bash
# HU-1925: recover 20260817 block 1 (turns 41-80) after raising gist
# maxTokens 12000->24000 (12k starved this dense research block 3 passes
# in a row: every call returned 0 chars, finishReason=length).
set -u
cd /opt/tencentdb-memory/MemoryCore || exit 1
LOG=/root/repos/huible/logs/hu1925-backfill.log
DONE=/root/repos/huible/logs/hu1925-backfill.done
SNAP=/root/repos/huible/logs/hu1925-gist-final-snapshot.txt
export TDAI_GATEWAY_CONFIG=/opt/tencentdb-memory/.config/tdai-gateway.yaml

while pgrep -f "backfill-gists-hu-1925/backfill.ts" >/dev/null 2>&1; do sleep 30; done

echo "[final3] 20260817 re-run (maxTokens 24k) start ($(date -u +%FT%TZ))" >> "$LOG"
npx tsx scripts/backfill-gists-hu-1925/backfill.ts --session 20260817_044013_27138159 >> "$LOG" 2>&1
rc=$?
blocks=$(python3 -c "import json;print(len(json.load(open('/root/.memory-tencentdb/memory-tdai/gists/20260817_044013_27138159.json'))))" 2>/dev/null || echo 0)
echo "20260817_044013_27138159 final3-rerun-24k rc=$rc blocks=$blocks" >> "$DONE"
echo "[final3] 20260817 re-run done rc=$rc blocks=$blocks ($(date -u +%FT%TZ))" >> "$LOG"

{
  echo "HU-1925 final gist-state snapshot ($(date -u +%FT%TZ))"
  python3 - <<'PY'
import json, glob
for f in sorted(glob.glob('/root/.memory-tencentdb/memory-tdai/gists/*.json')):
    d = json.load(open(f))
    flagged = [b['blockIndex'] for b in d if b.get('contractMiss')]
    degenerate = [b['blockIndex'] for b in d if not (b.get('arc') and b.get('enteredRecord'))]
    print(f"{f.split('/')[-1]}: blocks={len(d)} flagged_cm={flagged or 'none'} degenerate={degenerate or 'none'}")
PY
} > "$SNAP" 2>&1
echo "[final3] snapshot refreshed at $(date -u +%FT%TZ)" >> "$LOG"
