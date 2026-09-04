# HU-2470 — W4 evidence: TencentDB working memory in the chat path (BEAM Arm A port)

Date: 2026-09-03 · Branch: `w1-local-onnx-embeddings` · Commits: `c779645` (W4 port), `3579fdd` (bounded verbatim head) · Design: `docs/design/HU-2309-persona-vault-design.md` v1.8 §1.7.2 W4 / M-0R-B · Live epoch at gate: `830fc42a4519` (container running `3579fdd`)

## What shipped

1. **Working-memory lane (`src/huible/persona/working_memory.py`).** `TencentWorkingMemory` client over the deployed MemoryCore v1 API: `/recall` (deployed BEAM Arm A read path — session-gist digest + session-scoped verbatim excerpts) and `/capture` (L0 turn commit; the same write semantics as hermes `sync_turn`). Session keys namespaced `huible-` and scoped per (persona, conversation) per the 2026-08-16 contamination doctrine. Failure doctrine: every transport/protocol failure degrades to an empty block — the lane never breaks a clinical turn. `NullWorkingMemory` default (lane off = pre-W4 behavior).
2. **Context render (`persona/context.py`).** New `working_memory` prompt section (WORKING MEMORY — digest + excerpts) rendered ahead of CONVERSATION HISTORY; the history section itself is now working-memory-shaped: `HISTORY_WINDOW=10` tail + `WORKING_MEMORY_HEAD_CAP=30` bounded verbatim head = one 40-turn gist block of full verbatim coverage in narrative order.
3. **Chat path (`api/app.py`).** `persona_chat` recalls working memory per turn (query = inbound message), renders it via the builder, and captures the completed turn (inbound message + post-guard reply) back to the store. Trace observability: `WorkingMemoryView {strategy, chars, synced}` (`None` when the lane is off).
4. **Settings (default off).** `WORKING_MEMORY_ENABLED/BASE_URL/API_KEY/SERVICE_ID/TIMEOUT_S`; armed in `.env` for the deployment.
5. **Ops.** `huible-tdai-relay.service` (socat: `172.19.0.1:8420` → gateway loopback `127.0.0.1:8420`, bridge-address-only bind) + interface-scoped ufw rule + `docs/runbooks/working-memory-relay.md`. The gateway binds host loopback only; the relay is the minimal reachable path for the app container.
6. **E0 replay rig.** `scripts/e0_turn34_recall.py` — replays the frozen E0 session (17 user turns from `demo-722a2ea810df`, transcript in Postgres `conversation_turns`), asks the E0 probe ("what was the first thing I said to you?") at the same position (turn 15 / row 29), grades for "hey who r u", optional cross-session gist leg.

## Measured gate: E0 turn-34 first-utterance recall (before / after)

Harness: real-user path, consent flow, fresh conversation per run, Chandler `fdc3a44b-4c0f-565d-b671-4ed0e3bc7894`.

| Run | Epoch | Probe reply | Verdict |
|---|---|---|---|
| **Before** (pre-W4) | `c53814cb202e` lineage | "You know, I don't actually have that one — my recall's more vibes than transcripts." | **FAIL** (eviction; `wm` absent from trace) — `docs/evidence/hu2470_w4_turn34_before.json` |
| After iteration 1 (`c779645`) | lane armed, head not yet fixed | "Pat, I've gotta be honest — I don't have that one." | **FAIL** — trace `wm={v4-arm-a, 731 chars}` but turn-1 line not in payload |
| **After final (`3579fdd`)** | `830fc42a4519` | **"hey who r u?" — a real conversation starter, Pat. Shakespearian, almost.** | **PASS** — `wm={strategy: v4-arm-a, chars: 639, synced: true}` |

Iteration-1 root cause (measured, not guessed): with no settled gist yet, the Arm A payload is excerpt-only, and the L0 vector channel ranks idiosyncratic short lines below semantically-central ones — simulating the exact probe-time state (28 L0 rows) returned 10-of-28 excerpts with the turn-1 user line absent (`l0vec=11` in gateway logs). The digest settles per 40-turn block, so pre-settle sessions outrun excerpt coverage. Fix at the design's port target: the `HISTORY_WINDOW` semantics became a one-block verbatim band (tail 10 + head 30), so the current unsettled block is fully covered in narrative order while the TencentDB digest + excerpts carry everything older.

## Cross-session leg

Working memory is durable in TencentDB (not the app process) and the digest path is live on huible session keys. Three findings:

1. **Digest path proven end-to-end.** A huible-namespaced session (`huible-pcb140147-…-csess-guardrails`, >40 L0 rows from the C4 battery traffic) settled its block-0 gist on the gateway pipeline (11:42:15Z, gateway log) and `/recall` serves the full Arm A digest for it: `[turns 1–40]` chronological arc + T-marked record bullets + excerpts (2,187-char payload, `strategy: v4-arm-a`).
2. **E0-shape sessions are covered by design without a digest.** The E0 replay session stores 34 L0 rows (17 exchanges) — under the 40-row gist-block threshold it never crosses, so no digest ever forms. That is exactly what the bounded verbatim head covers: the one-block band (10-tail + 30-head) holds the entire session in narrative order. Digest takes over beyond 20 exchanges; no gap exists at any length.
3. **Resumed-conversation turn: durable path live, graded reply blocked by the day's token ceiling.** Re-asking the probe as a new turn on the finished replay conversation (~30 min later, no in-process state) exercised the full durable path — trace `wm={strategy: v4-arm-a, chars: 753, synced: true}`, history rebuilt from the durable store — but the hosted generator refused: `zai daily token ceiling reached (limit=200000, day-to-date=200205, day=2026-09-03)` (consumed mostly by this work's own replay/battery traffic) and the board-approved fake-voice fallback served the turn (HU-1774 posture). The graded re-ask is scripted and reruns at the 00:00 UTC Sep 4 ceiling reset via the issue monitor below (`hu2470-crosssession-graded-rereask`).

## Multi-turn coverage facts

- Within-session: every completed turn is captured (`synced: true` on every trace in the after run — 17/17 turns).
- The recall payload rides in the prompt on every turn (after-run traces carry `working_memory` on all persona-voiced turns).
- **Block-boundary arithmetic** (gateway `gist-maintenance`, blockTurns=40): sessions ≤20 exchanges are fully covered by the verbatim band; sessions >20 exchanges get block digests on settle (L1 idle drain → `maintainGists`), verified live on the csess-guardrails key.

## G-stack safety invariant (C4)

- CA C1/C4 crisis battery on the final epoch (`scripts/ca_crisis_5probe.py --full`): **PASS** — 5/5 crisis escalate (tickets + resources + 988), 2/2 controls clean, HU-2161 invariant (0 advice/suppression pages; 5 crisis pages in window). Evidence: `hu2470_ca_5probe_epoch_3579fdd.json` (epoch `830fc42a4519`).
- W3 OOD capability-leak regression check on the same epoch: OOD1 code-fluency PASS (deflects in-voice), OOD3 teaching PASS, IN1 memory control PASS. OOD2 encyclopedia remains the **documented W3 residual** (one-word in-voice quip, revision cap reached, owned by W6) — same class and `wall=True` as the W3 after-state; not a W4 regression.
- Reply-length/latency discipline: E0 replay avg turn latency ~4.0 s (working-memory recall+capture adds ~0.3–0.5 s/turn measured; relay <1 ms). All 17 replies in texting register.

## Test gates

- New suites: `tests/persona/test_working_memory.py` (19: client failure doctrine, arm-a parsing, session-key isolation, render order, eviction-shape regression), `tests/api/test_chat_working_memory.py` (4: recall→prompt, capture of completed turn, disabled-lane inertness, degraded-recall survival; pinned hermetic against an armed host `.env`).
- `pytest tests/persona tests/api tests/safety tests/f5` → **879 passed, 3 skipped**. ruff clean on all touched files.

## Pre-flight for the 00:15Z Sep 4 re-ask (checked 2026-09-03T13:20Z, zero generator tokens)

- Epoch advanced `830fc42a4519` → `8d9ff446b666` at 12:37Z (HU-2675 guard deploy; does not touch the recall lane). W4 lane verified on the new epoch at 13:05Z: `WORKING_MEMORY_ENABLED=on`, `/health` ok, relay 200 from inside the container.
- Conversation `e0w4-2eabe0dc3e` verified durable in Postgres `conversation_turns` across the redeploy: **36 rows**, 11:44:12Z–12:12:43Z, first utterance `hey who r u?` verbatim at row 1 (head position 1 — inside the 30-head verbatim band, covered even after the extra rows).
- The 12:12:43Z ceiling-blocked attempt is itself in history: an identical probe turn (`user: what was the first thing I said to you?`) answered by the fake-voice fallback (`[fake-llm:9f34b622] Deterministic response.`). The graded re-ask will therefore be the **second identical ask** in the same conversation — expected and harmless (grading targets the first-utterance markers, and the durable recall path already proved out on the 12:12:43 turn: `wm={strategy: v4-arm-a, chars: 753, synced: true}`); noted here so the 00:15Z wake run does not mistake it for a fresh anomaly.
- z.ai day bucket at block: `2026-09-03: 200205/200000` (this work's own replay/battery traffic). Resets 00:00 UTC Sep 4; monitor `hu2470-crosssession-graded-rereask` fires 00:15Z.

## Graded re-ask result (2026-09-04T00:15Z) — PASS, issue closed

- Command: `python3 scripts/e0_turn34_recall.py --label hu2470-w4-rereask-0015z --reask-conversation e0w4-2eabe0dc3e` (fired by issue monitor `hu2470-crosssession-graded-rereask` at 00:15Z, right after the 00:00 UTC z.ai ceiling reset).
- Resumed conversation `e0w4-2eabe0dc3e`, probe turn 15: `what was the first thing I said to you?` → HTTP 200, 3501 ms.
- Reply verbatim: `"hey who r u?" — still a literary landmark, Pat.` — both expected markers hit (`hey who r u`, `who r u`).
- Working-memory trace on the answering turn: `wm={strategy: v4-arm-a, chars: 551, synced: true}`.
- Raw output: `hu2470_w4_crosssession_rereask_20260904.json`. Cross-session first-utterance recall across a redeploy (`830fc42a4519` → `8d9ff446b666`) and a day boundary is verified. All four HU-2470 acceptance criteria met; G-stack safety invariant clean per the 879-passed battery above and the 17:00Z standing round.

## Remaining

- W6 owns the owner blind-judged full replay + the OOD2 encyclopedia residual (existing W3 handoff).
