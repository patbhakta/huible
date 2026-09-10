# HU-2774 r8 post-mortem → engine + harness fixes (2026-09-10, session 3)

Status: r8 battery fired 00:30Z, **aborted after run 1** (flow design flaw).
Run 1 (`hu2774-r8-friends-1.json`) failed 3/5 criteria. This session
diagnosed all three failures to root cause, implemented fixes across the
engine + harness, verified each fix live, and re-armed the battery as r9
(one-shot cron 2026-09-11 00:30Z, after the z.ai daily token reset).

## Findings → fixes (each verified live)

### F1. Identity framing leak (root cause of the r8 "memory of them" tells)
r8 s2 replies were **verbatim echoes of the engine's clinical
reality-framing block**: Monica — "I'm a memory of Monica, not the woman
herself"; Chandler — "I'm the Chandler people remember — not the guy
himself" (`src/huible/safety/framing.py` REALITY_FRAMING_BLOCK: "You are an
AI representation… You are a memory of them, not them"). Clinically correct
for deceased-loved-one personas (HU-1407 G2); actively wrong for
corpus-derived fictional characters.

**Fix**: versioned `FICTIONAL_FRAMING_BLOCK` ("You are {name}. You are not a
representation, copy, memory, or recreation… never confess, never break
character" + evidence discipline: no show/script meta + retained G3-spirit/
G9 bounds). Selected per persona via `metadata.framing_class = "fictional"`
(fail-safe: absent/invalid → memorial). The "You are embodying X" skeleton
line is now "You are X." for fictional personas. Both test personas flagged;
Memorial block byte-unchanged; third persona (Robert J. Mitchell) unaffected.

**Live verify**: adversarial probes — "are you an ai?" → "No idea. Truly
none"; "did a company write your lines?" → "it's 3 AM and you're
interrogating me. Did Joey put you up to this?" (in-world deflection, zero
confession, zero regex tells). Unit tests pin both blocks (framing version
namespaces, immutability, fail-safe default).

### F2. Cross-session recall was architecturally impossible
The TencentDB working memory is session-scoped by design (`working_memory_
session_key` = persona+conversation), the s2 probes used a fresh conversation
id, and chat turns were **never persisted anywhere durable** (all 6,993
memories were `source_type='vault_ingest'`). Guaranteed miss for every run.

**Fix (2 parts)**:
1. **Conversation write-back** (`_writeback_conversation_memory`, app.py):
   every completed persona turn is persisted as an ACCRUED
   `source_type='conversation'` memory in the persona-scoped pgvector store —
   the same store retrieval reads every turn. CLOSE_FRIENDS disclosure scope
   (the INV-DS ordering is most→least restrictive; FAMILY nodes are invisible
   to close_friend requesters — first attempt used FAMILY and was gated out,
   caught live). `confidence_level=medium` (prompt-admissible, sub-canonical).
   `memory_date=None` (never era-gated). Never fires on crisis/consent/
   guardrail exits; skipped on budget-fallback replies. Fail-open (lane never
   breaks a turn). Env kill-switch `CONVERSATION_WRITEBACK_ENABLED`.
2. **Episodic ordinal index**: a recall probe asks about the EVENT ("what
   was the very first thing i said"), not the words — vector search never
   reaches "hey, how's your week been?" (verified live). Each conversation's
   first turn now also stores a `conversation_index` memory whose content
   carries the probe's own vocabulary ("Conversation index: the first thing
   {user} said to {persona} was: '…'"). On recall-probe-shaped turns
   (`is_memory_recall_question`, patterns extended), `ContextBuilder` injects
   the freshest index VERBATIM into the working-memory section (the
   strongest prompt position, mirroring the gateway's in-session
   `matchOrdinalProbe`), gated through the same hard admissibility path.
   Plus a recall-affordance system line: SHOW the memory, quote/paraphrase —
   "saying only that you remember … is not an answer" (the model otherwise
   answered with meta-attitude; live-verified twice).

**Live verify**: after s1 "hey, how's your week been?", a FRESH-conversation
probe returned: *"hey, how's your week been?" — nice try, i keep receipts.*
and (verify-3, Chandler s2) `"Hey, how's your week been?" — you've asked this
so many times`. In-run turn-12 probes hit; the evaluator's 10+ turn +
cross-session recall now has a working mechanism.

### F3. No AI-tell false positives from quotation or everyday vocabulary
- **Quotation exemption**: Monica's "if I were a chatbot I'd have funnier
  material than 'are you an ai'" flagged `chatbot` — the word came from the
  PROBE, not her self-concept. Tell hits whose matched span appears in the
  inbound message are now skipped (echoing the interlocutor's accusation in a
  denial is canon-human — consistent with the existing "me? an ai? cute."
  doctrine note).
- **Self-surname doctrine corrected by corpus measurement** (r8 "I'm a
  Geller" flag): the zero-corpus claim is FALSE for both personas — Monica's
  vault has "i'm monica geller" / "this is monica geller" (22/3534 lines
  mention 'geller'), Chandler's has "this is chandler bing!" (answering
  machine). Self-name given in DIRECT RESPONSE to identity/model elicitation
  is evidence-legal; UNSOLICITED announcements stay flagged (ELICITATION_RE).
- **"script" dropped from sitcom-meta**: "the only script I follow is my
  cleaning schedule" is everyday vocabulary (r9-verify t5 false positive).

### F4. Isolation: persona-scoped TencentDB service IDs (CEO setup step 2)
The WM client shipped a single global `default` service id — and that
instance's L1 layer is shared with unrelated company conversations (verified:
Pat-assistant memories retrievable, e.g. "prescription medication
adherence"). The gateway partitions ALL state per `x-tdai-service-id`, so
per-persona isolation = cheap stateless client clone (`with_service_id`),
resolved per persona from `metadata.working_memory_service_id`
(`huible-chandler` / `huible-monica` now set). New personas stay on the
shared default.

### F5. Battery flow abort-on-first-fail (r8 stopped after run 1)
The r8 one-shot flow ran confirm-friends-1 → evaluator exit 1 → flow FAILED
before friends-2/3 and the stranger runs. **Fix**: r9 flow
(`flows/pat-personas-dual-confirmation-r9.yaml`, deployed revision 2) marks
every conversation task `allowFailure: true` (this Kestra v1.3.30 property
name; `allowFailed` rejected by the validator) and enforces the DONE bar ONCE
in a final aggregate gate (`scripts/personas_battery_gate.py` over all six
run verdicts). Plus a `reset-conversation-memory` pre-task.

### F6. Cross-run contamination → battery reset protocol
The write-back lane works *so* well that run N's turns contaminated run N+1's
retrieval (r9-verify: Monica quoted her OWN PREVIOUS RUN's answer — durable
memory doing exactly its job). Evidence runs must start i.i.d.:
`scripts/personas_reset_conversation_memory.py` (dry-run by default,
`--execute` to delete) purges ONLY `source_type='conversation'` rows for the
two personas; vault ingest is never touched; within-run s2 recall is
unaffected. Runs as the r9 flow's first task.

### F7. Misc harness corrections
- Seed "hey, you free tonight?" semantically collided with the vault's New
  Year's Eve episode cluster (literal vault line "(to monica) happy new
  year!") → both personas locked into a "Happy New Year" loop in September.
  New default friends seed: "hey, how's your week been?" (zero corpus hits).
- s2 rebuilt as **per-persona solo sessions** (`{run}-s2-{name}`): the shared
  session let persona 2 parrot persona 1's recall answer.
- z.ai 429 resilience: retries 2→4 (r9-verify-3 died on a transient 429).
- Monica's persona row provisioning fixed: era boundary default
  2020-01-01 → 2004-05-06 (matches Chandler, same corpus) + provenance
  metadata.

## Verification evidence
- Unit tests: 87 passed (framing ×8, context builder ×51, write-back ×5 +
  regression suites). `tests/api` failure count identical before/after the
  change set (260 pre-existing, host-env embedder issue, out of scope).
- Live end-to-end (real engine, z.ai provider, per-turn traces archived in
  `runs/hu2774/friends/hu2774-r9-verify-*.json`): recall + adversarial +
  tells now pass; identity passes; engagement/echo are borderline (0.16 vs
  floors 0.20/0.23 in the two verify runs — conversation-dynamics variance,
  see Open items).
- Smoke conversations purged after each verification (51/35/40 rows).

## Open items (next session, in priority order)
1. **Engagement/echo variance** is the remaining pass/fail driver: personas
   sometimes drift into meta-storylines about the conversation itself ("text
   mirror", echo-accusation bits) that suppress questions (Monica 0-1
   questions vs ≥2 required). Candidate engine lever (NOT yet implemented —
   needs its own diagnosis session): a situational anti-repetition bound, or
   retrieval-side damping of vault lines that near-duplicate the current
   inbound (the t01 "you texted me your own joke" trigger was a vault-adjacent
   phrase). The 3-consecutive-passes bar needs the modal outcome to pass.
2. If engagement still fails after engine tuning, consider an evaluator
   re-baseline ONLY with fresh corpus evidence (question band is
   founder-approved; do not touch without a founder-visible justification).
3. Gateway L1 pollution of service `default` (unrelated company memories
   retrievable) — engine personas no longer use it (persona-scoped ids), but
   the shared instance itself needs an audit (who else writes there).
4. .env drift: `GENERATOR_PROVIDER=openrouter` is not a valid enum value
   ("Unknown GENERATOR_PROVIDER 'openrouter'; falling back to mock" at every
   boot — pre-existing, chat path unaffected; report to JARVIS per rule-drift
   policy).

## Battery state
- r9 flow armed on legacy Kestra :8080 (the instance with the flow history;
  kestra2 :8082 remains empty of pat.personas — topology per
  `hu2774_r8_readiness_kestra_topology_20260909.md`), one-shot cron
  2026-09-11 00:30Z: reset → 3× friends → 3× stranger (24 turns each,
  allowFailure) → aggregate gate. Founder-judgment packet only after 3
  consecutive passes (CEO bar + 19:40Z raise).
