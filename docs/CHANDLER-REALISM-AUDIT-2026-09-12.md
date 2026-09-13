# Chandler Realism Audit — 2026-09-12 (CEO, during Pat's road day)

## Verdict
"Realism lost within minutes" was NOT the persona architecture — it was three
stacked operational failures. After fixes: 53/53-turn marathon session, real
Chandler voice, median 2.5s, 10/10 delayed memory recall after 40+ intervening
turns. The engine is chat-ready for long-horizon dogfooding.

## Root causes found (in firing order)
1. **Real-user ramp gate OFF** — `PERSONA_CHAT_REAL_USER_MODE=off` + traffic
   kill switch returned HTTP 503 SERVICE_DISABLED (crisis-line text) on every
   turn. Flipped to `open` (internal dogfood only; no external users).
2. **z.ai daily token ceiling exhausted (2M)** — the LLM client silently fell
   back to the `[fake-llm]` deterministic stub. This was the "dead" voice.
   Ceiling raised to 8M/day in `.env` (TEMPORARY — shared fleet key; real fix
   is a dedicated product key per HU-2243 key-separation doctrine).
3. **JSONB `.astext` comparator crash** — conversation-index lookup raised
   AttributeError every turn (memory recall silently degraded to "").
   Fixed: `as_string()` — commit `c1dd6d6`.
4. **Session dosage cap latch** — `RISK_DOSAGE_CAP_TURNS=40` paused the
   persona permanently for the session at turn 41 (clinical anti-binge
   guardrail, but no in-band re-entry exists). Set to `0` (disabled) pending a
   proper clinical re-entry design (distress-trend + crisis-history signals
   remain armed). Flagged for Clinical Advisor follow-up — see Open Items.

## Evidence
- Probe transcripts: /tmp/chandler-probe-real2.json (20-turn),
  /tmp/chandler-long.json (53-turn marathon incl. 10-probe memory exam).
- Marathon: 5 seeded facts → 38 banter turns → 10 delayed-recall probes.
  All 10 correct, in-voice, zero fabrications, zero AI-tells
  (Python request deflected: "I can barely alphabetize my CDs").

## Open items (next sprints)
1. Dosage-cap re-entry design (Clinical Advisor) — pause should offer a
   warm wind-down + next-session invite, not a dead latch. Also consider
   raising default from 20 once re-entry exists.
2. Dedicated product API key (stop sharing the fleet z.ai key).
3. Ops: heartbeat must alert on `fake-llm` fallback + kill-switch state —
   /health said "ok" through all three failures. (Alert on generator label,
   zai token ledger, and 503-rate.)
4. Multi-SESSION persistence test (new conversation_id, same persona — does
   TencentDB working memory carry across sessions? untested tonight).

## Addendum — long-horizon + multi-session results (23:45 UTC)

- Dosage cap disabled → 53/53-turn marathon, ZERO pause events, memory exam 10/10.
- **Multi-session persistence: PASS.** Fresh conversation_id, no carried context:
  Chandler recalled the recital date ("October 18th — circled in permanent marker"),
  the speech, Tuesday soccer practice, and the three-weekend faucet saga —
  all seeded in the *prior* session. This is the months/years engagement primitive.
- Sprint-3 lane states: codex Atlas live+hosted (8.5/9.5/9.0 blind audit);
  claude dist already has all 6 atlas routes built (still finishing QC);
  antigravity committed generator early, iterating.

## Addendum 2 — Gemini/Antigravity-lane voice swap attempt (00:05 UTC, Sep 13)

Boss order: "Have Chandler switch to antigravity if needed" (i.e. move the persona
voice off z.ai onto the Gemini/Antigravity lane key).

**Attempted blind env swap → REVERTED within ~10 min.** Three blockers found:
1. Google geo-block: engine must egress via pat-w11pc SOCKS relay. Global
   ALL_PROXY/HTTPS_PROXY vars broke the engine's own internal urllib calls
   (working-memory /recall: "unknown url type: socks5") — proxy must be scoped
   to the LLM client only (httpx transport), never global env.
2. httpx lacked the socks extra → fixed properly: pyproject httpx[socks] (committed).
3. Gemini API 400: persona request builder leaks the z.ai `thinking` field into
   the Gemini payload (GENERATOR_EXTRA_JSON) — needs per-provider payload guards.

**State: reverted to the PROVEN zai glm-5.3 config; re-verified alive (2-3s replies).**
Proper fix queued as a lane card: scoped SOCKS transport in GeminiLLMClient +
provider-guarded payload + fallback ladder gemini→zai (never fake). Also notes:
investinme-lane Gemini quota status unknown post-refill; Antigravity CLI itself
is a coding agent, not a serving model — "switch Chandler to antigravity" maps
to the Gemini key its lane uses, not the agy CLI.
