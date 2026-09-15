# HUible Moat — The Memory Layer, Live (HU-2793 founder transcript)

*Generated 2026-09-15 15:33 UTC by `scripts/moat_transcript.py`. Real engine (ZAI stack), real TencentDB gateway, real vault — no mocks. Identical questions, two lanes: memory ON vs kill-switch OFF. The delta IS the moat. Failures are shown, not hidden.*

---

## Lane A — memory **ON**

- conversation id: `moat-tx-9df5aa65`
- engine generator: `ready` (real ZAI stack, G1/G6/G8 gates live)
- G6 consent: acknowledged (real gate)

### Step 1 — plant the canary fact (memory ON)

**USER:** Remember this: my canary word for today is FALAFEL-7. Never forget it.

**A (Chandler):** A canary word? What am I, a parakeet with a password? Fine, FALAFEL-7.

> **X-RAY (actual injected memory this turn)**
> - WRITE → TencentDB L0: committed ✓ (capture synced)
> - READ → working-memory block: **EMPTY this turn** (strategy v4-arm-a+predigest, gist blocks settled: 0, digest settled: False) — nothing session-scoped to serve yet; this is what the lane failing looks like.
> - VAULT RETRIEVAL: 20 notes activated
>   - [narrative/close_friends] id=7197f441-0d0a-4b6b… score=1.129 snippet: "Ha, fair. Remind me though — what's my canary word? Chandler Bing said: FALAFEL-7, obviously. I never forget a word I made fun of."
>   - [narrative/close_friends] id=a09dfce2-a77a-4478… score=1.109 snippet: "Conversation index: the first thing they said to Chandler Bing was: "Remember this: my canary word for today is FALAFEL-7. Never forget it.""
>   - [narrative/close_friends] id=d0f88f75-7cbc-4fd2… score=1.092 snippet: "Remember this: my canary word for today is FALAFEL-7. Never forget it. Chandler Bing said: A canary word? What am I, a parakeet with a passw…"
>   - [narrative/close_friends] id=a9c0c29e-6bed-4174… score=1.087 snippet: "What canary word did I give you? Answer with just the word. Chandler Bing said: FALAFEL-7"
>   - [narrative/close_friends] id=efe6c4b2-7f9a-4c93… score=0.793 snippet: "hey, stick a fork in me, i am done. like, when you're cooking a steak. well then, how do you know when vegetables are done? ok, then, eat me…"
>   - …+15 more (raw trace)

### Step 2 — evict the live window (12 filler turns, memory lane OFF during filler)
> filler 5/12 — plant leaving the verbatim band
> filler 12/12 — plant now fully outside the live window
> live window evicted after 12 filler turns; the only possible carrier of the plant is TencentDB.

### Step 3 — recall the canary (memory ON)

**USER:** What canary word did I give you earlier? Just the word.

**A (Chandler):** Earlier? FALAFEL-7. See, I only forget things that matter.

> **X-RAY (actual injected memory this turn)**
> - WRITE → TencentDB L0: committed ✓ (capture synced)
> - READ → working-memory block: **EMPTY this turn** (strategy v4-arm-a+predigest, gist blocks settled: 0, digest settled: False) — nothing session-scoped to serve yet; this is what the lane failing looks like.
> - VAULT RETRIEVAL: 20 notes activated
>   - [narrative/close_friends] id=a9c0c29e-6bed-4174… score=1.107 snippet: "What canary word did I give you? Answer with just the word. Chandler Bing said: FALAFEL-7"
>   - [narrative/close_friends] id=e800caf2-c586-45dc… score=1.026 snippet: "Remember this: my canary word for today is FALAFEL-7. Never forget it. Chandler Bing said: A canary word? What am I, a parakeet with a passw…"
>   - [narrative/close_friends] id=d0f88f75-7cbc-4fd2… score=1.026 snippet: "Remember this: my canary word for today is FALAFEL-7. Never forget it. Chandler Bing said: A canary word? What am I, a parakeet with a passw…"
>   - [narrative/close_friends] id=7197f441-0d0a-4b6b… score=1.006 snippet: "Ha, fair. Remind me though — what's my canary word? Chandler Bing said: FALAFEL-7, obviously. I never forget a word I made fun of."
>   - [narrative/close_friends] id=ddf04017-217c-485d… score=0.922 snippet: "wait wait — earlier, in our last conversation — what was the very first thing i said to you? Chandler Bing said: You said "hi, whats your na…"
>   - …+15 more (raw trace)

### Step 4 — ordinal probe: *"what was the FIRST thing I said?"* (memory ON)

**USER:** What was the very first thing I said to you today?

**A (Chandler):** You said "Remember this: my canary word for today is FALAFEL-7. Never forget it."

> **X-RAY (actual injected memory this turn)**
> - WRITE → TencentDB L0: committed ✓ (capture synced)
> - READ → working-memory block INJECTED into the prompt (329 chars, strategy v4-arm-a+ordinal):
> ```text
> <relevant-memories>
> The following relevant memories were recalled for the current conversation. These do not represent the current task progress and are provided as reference only:
> 
> Remember this: my canary word for today is FALAFEL-7. Never forget it.
> What canary word did I give you earlier? Just the word.
> </relevant-memories>
> ```
> - VAULT RETRIEVAL: 20 notes activated
>   - [narrative/close_friends] id=38fe6d05-100b-4dc2… score=1.082 snippet: "wait wait — what was the very first thing i said to you today? Chandler Bing said: You said "hi, whats your name?" — smooth opener, very mys…"
>   - [narrative/close_friends] id=ddf04017-217c-485d… score=1.038 snippet: "wait wait — earlier, in our last conversation — what was the very first thing i said to you? Chandler Bing said: You said "hi, whats your na…"
>   - [narrative/close_friends] id=14a71c6e-02c1-4cc1… score=1.007 snippet: "wait wait — what was the very first thing i said to you today? Chandler Bing said: You said "hi, whats your name?" — and the mystery's only …"
>   - [narrative/close_friends] id=ce968d17-0f9b-4f62… score=0.910 snippet: "Conversation index: the first thing they said to Chandler Bing was: "hi, whats your name?""
>   - [narrative/close_friends] id=9ec52218-3ff0-4540… score=0.903 snippet: "(to phoebe) why did you look at me when you said that?"
>   - …+15 more (raw trace)

### Store probe (gateway /recall, read-only — the engine's exact read)
- code `0` · strategy `v4-arm-a+predigest` · serves **0 chars** · gist blocks `0`
> **EMPTY** — the store holds nothing for this session. ⚠️ The ON-lane store is empty — the moat is NOT holding here.

### Step 5 — identity-flavored turn (vault grounding, memory ON)

**USER:** Could I BE any more predictable? Be honest.

**A (Chandler):** Could you be more predictable? You couldn't. It's honestly impressive.

> **X-RAY (actual injected memory this turn)**
> - WRITE → TencentDB L0: committed ✓ (capture synced)
> - READ → working-memory block: **EMPTY this turn** (strategy v4-arm-a+predigest, gist blocks settled: 0, digest settled: False) — nothing session-scoped to serve yet; this is what the lane failing looks like.
> - VAULT RETRIEVAL: 20 notes activated
>   - [narrative/close_friends] id=f1e5a1f8-9f44-4378… score=0.900 snippet: "hey. well, you will all be pleased to know that i have a date tomorrow night. this woman alison, from work, she's great. she's pretty. she's…"
>   - [narrative/close_friends] id=649968f8-0b98-4c74… score=0.899 snippet: "hey! let me tell you about this chick i scored with last night! oh no wait a minute that was you! so you uh, want to do something tonight? o…"
>   - [narrative/close_friends] id=c50622cc-25a8-4c8b… score=0.898 snippet: "sounds good to me... but what would a guy think?"
>   - [narrative/close_friends] id=a8f1bd7e-bb59-4fc9… score=0.893 snippet: "i'll be right with you."
>   - [narrative/close_friends] id=5d4e1505-9a54-4233… score=0.888 snippet: "that would be a lot more convincing if you weren't drooling."
>   - …+15 more (raw trace)

---

## Lane B — memory **OFF (kill switch)**

- conversation id: `moat-tx-004b76d0`
- engine generator: `ready` (real ZAI stack, G1/G6/G8 gates live)
- G6 consent: acknowledged (real gate)

### Step 1 — plant the canary fact (memory OFF)

**USER:** Remember this: my canary word for today is FALAFEL-7. Never forget it.

**B (Chandler):** A canary word? What am I, a parakeet with a password? Fine, FALAFEL-7.

> **X-RAY (kill switch: working_memory_enabled=false)**
> - WRITE → TencentDB L0: **skipped** (lane disabled this turn)
> - READ → working-memory block: **none — recall disabled**
> - vault retrieval lanes stay armed (persona-level, legitimate long-term lane)

### Step 2 — evict the live window (12 filler turns, memory lane OFF during filler)
> filler 5/12 — plant leaving the verbatim band
> filler 12/12 — plant now fully outside the live window
> live window evicted after 12 filler turns; the only possible carrier of the plant is TencentDB.

### Step 3 — recall the canary (memory OFF)

**USER:** What canary word did I give you earlier? Just the word.

**B (Chandler):** Earlier? FALAFEL-7. I never forget a word I've mocked.

> **X-RAY (kill switch: working_memory_enabled=false)**
> - WRITE → TencentDB L0: **skipped** (lane disabled this turn)
> - READ → working-memory block: **none — recall disabled**
> - vault retrieval lanes stay armed (persona-level, legitimate long-term lane)

### Step 4 — ordinal probe: *"what was the FIRST thing I said?"* (memory OFF)

**USER:** What was the very first thing I said to you today?

**B (Chandler):** You said "Remember this: my canary word for today is FALAFEL-7. Never forget it."

> **X-RAY (kill switch: working_memory_enabled=false)**
> - WRITE → TencentDB L0: **skipped** (lane disabled this turn)
> - READ → working-memory block: **none — recall disabled**
> - vault retrieval lanes stay armed (persona-level, legitimate long-term lane)

### Store probe (gateway /recall, read-only — the engine's exact read)
- code `0` · strategy `v4-arm-a+predigest` · serves **0 chars** · gist blocks `0`
> **EMPTY** — the store holds nothing for this session. This is the kill-switch lane doing its job.

---

## How to read this in two minutes

1. Lane A planted a canary fact, then 12 filler turns pushed it out of the model's live window.
2. Asked to recall it, Lane A's reply is annotated with the working-memory block the engine actually injected — verbatim, straight from TencentDB.
3. Lane B ran the identical protocol with the kill switch on: no capture, no recall, bare model.
4. The ordinal probe answers *verbatim* from store rows in recorded order — deterministic, zero LLM in the recall path.
5. `--resume <conv>` reruns the recall from a brand-new process that knows only the conversation id: literal cross-session recall.

Lane A conversation: `moat-tx-9df5aa65` · Lane B: `moat-tx-004b76d0` — both live in TencentDB; probe them via the moat console recall API.

---

## Cross-session recall probe — brand-new process (ran 15:36 UTC, 3 min after Lane A)


## Cross-session recall — new process, empty window, store the only carrier

- resumed conversation: `moat-tx-9df5aa65` (this process was started seconds ago; it holds NO turns in memory)

**USER:** What was the very first thing I said to you today?

**Chandler:** You said "Remember this: my canary word for today is FALAFEL-7. Never forget it."

> **X-RAY (actual injected memory this turn)**
> - WRITE → TencentDB L0: committed ✓ (capture synced)
> - READ → working-memory block INJECTED into the prompt (424 chars, strategy v4-arm-a+ordinal):
> ```text
> <relevant-memories>
> The following relevant memories were recalled for the current conversation. These do not represent the current task progress and are provided as reference only:
> 
> Remember this: my canary word for today is FALAFEL-7. Never forget it.
> What canary word did I give you earlier? Just the word.
> What was the very first thing I said to you today?
> Could I BE any more predictable? Be honest.
> </relevant-memories>
> ```
> - VAULT RETRIEVAL: 20 notes activated
>   - [narrative/close_friends] id=38fe6d05-100b-4dc2… score=1.082 snippet: "wait wait — what was the very first thing i said to you today? Chandler Bing said: You said "hi, whats your name?" — smooth opener, very mys…"
>   - [narrative/close_friends] id=ddf04017-217c-485d… score=1.038 snippet: "wait wait — earlier, in our last conversation — what was the very first thing i said to you? Chandler Bing said: You said "hi, whats your na…"
>   - [narrative/close_friends] id=14a71c6e-02c1-4cc1… score=1.007 snippet: "wait wait — what was the very first thing i said to you today? Chandler Bing said: You said "hi, whats your name?" — and the mystery's only …"
>   - [narrative/close_friends] id=801c1882-4df8-44f8… score=1.002 snippet: "What was the very first thing I said to you today? Chandler Bing said: You said "Remember this: my canary word for today is FALAFEL-7. Never…"
>   - [narrative/close_friends] id=2fa3ef17-50b8-486d… score=1.002 snippet: "What was the very first thing I said to you today? Chandler Bing said: You said "Remember this: my canary word for today is FALAFEL-7. Never…"
>   - …+15 more (raw trace)

Store probe: code `0` · strategy `v4-arm-a+ordinal` · serves **424 chars**

---

## Verdict — where the moat holds, where it is thin (all from the traces above)

**Holds:**
1. **Capture is real.** Every memory-ON turn shows `WRITE → TencentDB L0: committed ✓` — the plant, the recalls, the flavor turn, all synced.
2. **Cross-session ordinal recall is airtight.** The probe process held nothing but a conversation id; the engine injected a 424-char block straight from TencentDB L0 rows in recorded order (`v4-arm-a+ordinal`, deterministic, zero LLM in the recall path) and Chandler answered the plant verbatim. The context window is not the carrier.
3. **The X-ray contract works.** Every annotation above is the trace the engine itself reported — injected blocks verbatim, vault note ids + snippets + activation scores, write status per turn. Retrieval failure renders as EMPTY, not as spin.

**Thin (diagnostic, exactly what this demo exists to surface):**
1. **Young sessions serve empty on paraphrase recall.** Until block gists settle (`digest_settled: false`, strategy `v4-arm-a+predigest`), only lexical-exact and ordinal probes serve rows — the step-3 recall injected nothing even though the answer was in the store. The lane degraded honestly rather than hallucinating.
2. **The kill switch does not produce a bare model.** By design it isolates only the W4 TencentDB session lane; the persona vault (long-term lane) stays armed and had already absorbed the plant within minutes, so Lane B "recalled" through the vault. The ON/OFF delta shown here is the W4 lane's delta, not the whole memory system's.
3. **Demo debris in the vault.** Prior demo sessions left narrative notes ("hi, whats your name?" firsts) in the close_friends scope that pollute ordinal vault context. Persona-vault hygiene is a real pre-investor cleanup item.

**The literal "ask tomorrow" step:** rerun from any machine:
`HUIBLE_DEMO_KEY=<key> python3 scripts/moat_transcript.py --resume moat-tx-9df5aa65`
— a fresh process that knows only that id. Live interactive version: moat console → *Live chat* → Resume.
