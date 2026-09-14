# HU-2793 — The Moat, Live (founder gate)

One URL. Zero explanation. The memory layer working in front of you.

## Open

```
http://100.101.235.117:8097/?t=huible-preview
```

Tailnet-only (same posture as the :8098 onboarding demo). Any device on the
mesh. First turn shows the real consent card (G6) — click acknowledge.

## What you are looking at

Every reply gets an **X-ray card** underneath it, verbatim:

| Row | What it shows |
| --- | --- |
| 1 · WRITE → TencentDB L0 | The exact user line + reply committed to the store this turn (or `NOT written` if the capture failed) |
| 2 · READ → injected block | The working-memory block the engine actually injected into the prompt — session digest + verbatim excerpts, character for character |
| 3 · VAULT RETRIEVAL | The vault notes grounding the reply (type, activation score, snippet) + which scoped lanes fired |

If a lane fails, the card shows it failing. Diagnostic, not sales pitch.

## The four proofs

1. **Amnesia test** — tell Chandler a secret word, then click
   *“Amnesia test — wipe page, ask what did I say first?”*. The page wipes,
   the store answers. Deterministic recall lane (zero-LLM), verbatim.
2. **Deep amnesia** (airtight, ~4 min) — 42 filler turns push your first line
   out of the model's live window entirely. Then the probe: the only place the
   answer can come from is the injected TencentDB block. Watch the block in
   the X-ray — the reply quotes it character for character.
3. **Kill switch** — flip *Memory OFF* and ask again: the injected block
   disappears from the X-ray (recall empty, capture skipped). Flip back —
   it returns. The delta is the moat being load-bearing.
4. **Isolation guard** — *“New conversation”* mints a fresh session key:
   the W4 session lane starts empty (contamination doctrine, 2026-08-16).
   The persona's durable vault memory legitimately persists per-persona —
   that's the long-term lane, shown separately in every X-ray.

## Verified live (2026-09-14, real engine, real path, ZAI voice)

- Plant → 42-turn eviction → probe: reply quoted turn 1 verbatim
  (`MAGNOLIA-42`), injected block on trace `v4-arm-a+ordinal`, 267 chars,
  capture synced.
- Kill-switch turn on the same session: `working_memory: null`, capture
  skipped, vault lane still armed.
- Burn: ~212k tokens for the whole verification session (ceiling 8M/day).

## Wiring (for the record)

- Demo server: `scripts/moat_demo_server.py` (repo), systemd unit
  `huible-moat-demo.service`, port 8097, token-gated, tailnet bind.
- Engine: chat trace now carries `working_memory.context` (the verbatim
  injected block) and honors `working_memory_enabled=false` per request
  (`scripts`/`tests` in HU-2793 commit 901409a + follow-ups).
- Store inspector: read-only gateway `/recall` probes with the engine's exact
  session keys (`huible-p<persona>-c<conversation>`).
