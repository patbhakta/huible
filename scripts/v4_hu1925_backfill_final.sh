#!/usr/bin/env bash
# HU-1925: final drain pass. Waits for the queue + hermes rerun to finish,
# re-runs 20260810 (its block 3 turns 121-160 was skipped as still-degenerate:
# both attempts produced 0 chars, glm-5.2 empty-output quirk is stochastic),
# then writes a final gist-state snapshot for the issue record.
set -u
cd /opt/tencentdb-memory/MemoryCore || exit 1
LOG=/root/repos/huible/logs/hu1925-backfill.log
DONE=/root/repos/huible/logs/hu1925-backfill.done
SNAP=/root/repos/huible/logs/hu1925-gist-final-snapshot.txt
export TDAI_GATEWAY_CONFIG=/opt/tencentdb-memory/.config/tdai-gateway.yaml

while pgrep -f "v4_hu1925_backfill_queue.sh" >/dev/null 2>&1; do sleep 30; done
while pgrep -f "v4_hu1925_backfill_rerun.sh" >/dev/null 2>&1; do sleep 30; done
while pgrep -f "backfill-gists-hu-1925/backfill.ts" >/dev/null 2>&1; do sleep 30; done

echo "[final] 20260810 re-run start ($(date -u +%FT%TZ))" >> "$LOG"
npx tsx scripts/backfill-gists-hu-1925/backfill.ts --session 20260810_040559_5985b592 >> "$LOG" 2>&1
rc=$?
blocks=$(python3 -c "import json;print(len(json.load(open('/root/.memory-tencentdb/memory-tdai/gists/20260810_040559_5985b592.json'))))" 2>/dev/null || echo 0)
echo "20260810_040559_5985b592 final-rerun rc=$rc blocks=$blocks" >> "$DONE"
echo "[final] 20260810 re-run done rc=$rc blocks=$blocks ($(date -u +%FT%TZ))" >> "$LOG"

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
echo "[final] snapshot written to $SNAP" >> "$LOG"
