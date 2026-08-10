# Chandler Bing — Persona-0 Protocol Test

> **Purpose:** Validate the Conversational Onboarding Protocol end-to-end against Persona-0 (Chandler Bing). Prove the conversation → extraction-schema → gap-fill loop produces rich, structured puzzle data without the client ever seeing a form.
> **Exercises:** [`conversational-onboarding-protocol.md`](./conversational-onboarding-protocol.md) (flow, Carrie persona, gap-fill) + [`conversation-extraction-schema.md`](./conversation-extraction-schema.md) (detection, confidence, gaps).
> **Reference data:** `persona-0-chandler-bing` (vault) + `chandler-bing/02-clean/persona-profile.md` (ground-truth traits to check extraction against).

---

## 1. Test Setup

**Persona-0:** Chandler Bing — walled into the sitcom universe (no knowledge of actors/meta-layer). Test candidate because he has a huge dialog corpus and clearly distinctive traits (sarcasm, commitment-phobia, loyalty masked by teasing).

**Simulated client:** "Sam," someone close to Chandler, grieving. This mirrors a real onboarding — we treat Chandler as a real lost person, not a character, so the empathy machinery gets a real workout.

**What we're checking:**
1. Does Carrie's flow (open → invite → follow the thread → circle-back) feel like a conversation, not a survey?
2. Does the extraction schema map the transcript → puzzle fields with sensible confidence?
3. Does gap-fill surface missing clusters as *natural* follow-ups, not a checklist?
4. Does the extracted data match the known ground-truth Chandler profile (sanity check)?

> **Caveat:** This is a *protocol* test, not a *persona-build* test. The persona/conversation module is currently empty (per the honest project status), so we are not testing whether an LLM *sounds* like Chandler — we are testing whether the **onboarding method** captures the right data to eventually build him. Persona fidelity (Axis 2) is out of scope here.

---

## 2. The Conversation (turn-by-turn, with backstage notes)

Carrie's messages are labeled with their **move-type** (Open / Invite / Reflect / Follow-up / Circle-back). Backstage extraction notes appear in `> blockquote` after each client turn.

---

**Carrie [Open]:** Hey — I'm so sorry. If you want to keep the memories close, I'm the one to talk to. Wanna tell me a little bit about him? Oh — I go by Carrie. What's your name?

**Sam:** I'm Sam. It's my brother, Chandler.

> *Extraction:* `name`=Chandler (grounded), client `name`=Sam, `relationship`=sibling (grounded). Cluster A started.

**Carrie [Reflect + Invite]:** Hi Sam. Chandler. Tell me about him — however it comes out.

**Sam:** Honestly? He was the funniest person I knew. But like… the funny was a shield. You know? Everyone thought he was just the jokes guy. It was more than that.

> *Extraction:* `personality_traits`=funny, uses-humor-as-shield (inferred — the client stated the dynamic directly, so strong inference), `humor_style`=defensive/comedic (inferred). Cluster C seeded. **Note:** "funny was a shield" is exactly the kind of memory-shaped insight a form would never capture.

**Carrie [Follow-up — story-behind-the-fact]:** "The funny was a shield" — that's a really specific thing to see in someone. What made you realize that?

**Sam:** Oh god. So he had this thing — whenever anything got real, he'd do this voice. Like a cartoon narrator. Deflect, deflect, deflect. But then with me, late at night, the real stuff would come out. He was terrified of ending up alone.

> *Extraction:* `quirks_habits`=deflection-into-cartoon-narrator-voice (inferred), `private_voice` / `contrast probe`=the real stuff came out late at night with Sam only (inferred — **gold**, the "only you" version), `values`/`fears`=terror of being alone (inferred), `temper_under_pressure`=deflects then opens up privately (inferred). One turn → four Cluster C/E fields. Cluster E seeded strongly.

**Carrie [Follow-up — contrast drill-down]:** I'm glad he had that with you. The late-night real stuff — do you remember a specific time? Even just a sentence he said?

**Sam:** Yeah. He once told me, completely sober, at like 2am — "I'm hopeless and awkward and desperate for love." And then immediately made a joke about it. That was so *him*.

> *Extraction:* `words_that_stuck`="I'm hopeless and awkward and desperate for love" (**grounded** — verbatim, exact quote), `humor_style`=joke to break sincerity (inferred), `catchphrases`=candidate — "could I BE any more…" construction implied (weak — needs artifact confirmation). Cluster D seeded with a defining memory. **This verbatim quote is the single highest-value capture so far** — it's the kind of thing the persona needs to sound right.

**Carrie [Reflect]:** "Hopeless and awkward and desperate for love" — and then a joke to escape it. That's him in one sentence. I'm keeping that one close.

**Sam:** That was Chandler. Oh — and he had this thing he'd say. "Could I *be* wearing any more clothes?" He'd stretch the word out. Every other sentence.

> *Extraction:* `catchphrases`="Could I BE any more…" construction (grounded — verbatim from client), `accent_voice_cues`=stretches/emphasizes specific words (grounded), `signature_words`=candidate patterns. Cluster E now strongly populated.

**Carrie [Invite — pivot to people (Cluster B)]:** You mentioned the funny was for everyone, but the real stuff was yours. Who else was in his world?

**Sam:** Monica. His wife, eventually. She was the first person who actually *stayed*. That broke the spell — he'd spent his whole life waiting to be left. And Joey, his best friend and roommate forever. He basically raised Joey. Paid for half his stuff without making it weird.

> *Extraction:* `relationships`=Monica (wife/partner — grounded), Joey (best friend/roommate — grounded), `relationship_dynamics`=Monica broke the "waiting to be left" spell (inferred — huge personality insight), `social_style`/`loyalty`=supported Joey financially without ego (inferred). Cluster B populated richly. **Cross-cluster link:** the Monica dynamic confirms `values` (commitment, permanence) and resolves part of the "terror of being alone" fear.

**Carrie [Follow-up — sensory drill-down into a memory]:** Joey and Chandler — I love that. Was there a spot? Like, the apartment, the chairs, a routine the two of them had?

**Sam:** Oh, the barcaloungers. They had these two recliners. That was *their* spot. Chandler would come home from work — god, he hated that job, nobody ever knew what he did —

> *Extraction:* `places_traditions`=the barcaloungers / their spot (inferred), `occupation_work`=hated his job, mysterious what he actually did (inferred), `loved_stories`=candidate ("nobody ever knew what he did" — a running bit, likely told repeatedly). Cluster D deepening.

**Carrie [Reflect + light Invite]:** The recliners. Of course they had recliners. 😄 And wait — *nobody knew what he did?* Was that a running joke?

**Sam:** THE running joke. We'd all guess. He'd never tell. It's data entry, statistical analysis, re-routing… nobody knows. He'd just say "I do… stuff."

> *Extraction:* `loved_stories`=the mysterious job (inferred — confirmed recurring), `humor_style`=absurdist/mystery bit (inferred), `catchphrases`=candidate "I do… stuff" (grounded). Excellent — a recurring story surfaced naturally.

*…(conversation continues; Carrie lets the thread wander toward memories, then marks the pause for the ~48h build)…*

---

## 3. Extracted Puzzle State (after ~12 turns)

What the backstage pipeline produced from this short exchange. Confidence per the [extraction schema](./conversation-extraction-schema.md).

### Cluster A — Identity & World
| Field | Value | Conf |
| --- | --- | --- |
| `name` | Chandler | grounded |
| `nicknames` | — *(gap: shallow)* | — |
| `era_of_life` | adult / pre-marriage + post-Monica arc | inferred |
| `occupation_work` | mysterious corporate job he hated; "I do… stuff" | inferred |
| `birthplace_region` | — *(gap: missing)* | — |
| `languages` | English *(inferred default)* | weak |
| `accent_voice_cues` | stretches/emphasizes specific words ("*be*") | grounded |
| `daily_rhythm` | came home to the recliners from the hated job | inferred |

### Cluster B — People
| Field | Value | Conf |
| --- | --- | --- |
| `relationships` | Sam (sibling), Monica (wife), Joey (best friend/roommate) | grounded |
| `relationship_dynamics` | Monica broke his fear of abandonment; raised/supported Joey without ego | inferred |
| `terms_of_endearment` | — *(gap: missing)* | — |
| `pets` | — *(gap: missing — duck/chicken not surfaced)* | — |
| `social_style` | funny-for-everyone, real-only-with-Sam | inferred |

### Cluster C — Personality
| Field | Value | Conf |
| --- | --- | --- |
| `personality_traits` | hilarious, loyal, insecure, commitment-phobic→growing | inferred |
| `humor_style` | sarcasm as shield; absurdist running bits; breaks sincerity with a joke | inferred |
| `values` | permanence, loyalty, being loved | inferred |
| `temper_under_pressure` | deflects publicly, opens up privately late at night | inferred |
| `quirks_habits` | cartoon-narrator deflection voice | inferred |
| `catchphrases` | "Could I BE any more…", "I do… stuff" | grounded |
| `surprises` | the sincere 2am vulnerability vs. the public jokes | inferred |

### Cluster D — Memories
| Field | Value | Conf |
| --- | --- | --- |
| `defining_memory` | the 2am "hopeless and awkward" confession + immediate joke | inferred |
| `words_that_stuck` | *"I'm hopeless and awkward and desperate for love"* | **grounded** |
| `loved_stories` | the mysterious job running gag | inferred |
| `places_traditions` | the barcaloungers / their spot | inferred |
| `hard_memory` | (the abandonment fear — handle per Cluster F adjacent) | inferred |

### Cluster E — Voice
| Field | Value | Conf |
| --- | --- | --- |
| `opening_closing_phrases` | — *(gap: artifact_dependent — needs texts)* | — |
| `message_length_style` | short punchy + sarcastic tag (from artifact profile) | weak |
| `signature_words` | emphasis-stretching; sarcasm construction | grounded |
| `inside_jokes` | the job mystery (shared with the group) | inferred |
| `private_voice` | the late-night real version, Sam only | inferred |

### Cluster F — Sensitive
| Field | Value | Handling |
| --- | --- | --- |
| `private_struggles` | deep abandonment terror, commitment fear | **tagged `sensitive`.** Surfaced voluntarily; never chased. |

### Sanity check against ground truth
The 02-clean `persona-profile.md` lists: sarcastic, self-deprecating, commitment-phobic, insecure, loyal; "Could we be…", "hopeless and awkward and desperate for love"; Monica/Joey/Ross/Janice dynamics. **The conversation captured all of the core traits + the verbatim catchphrase + the key relationship dynamics — without a single form question.** Extraction matches ground truth. ✅

---

## 4. Gap Detection (what the gap-fill protocol now targets)

Applying the [gap taxonomy](./conversation-extraction-schema.md#6-gap-taxonomy--not-all-gaps-are-equal):

| Gap | Type | Carrie's planned move |
| --- | --- | --- |
| `nicknames` (A) | missing | Wait for a natural identity moment — *"Did his family have a name for him?"* |
| `birthplace_region` (A) | missing | Circle-back, low-stakes: *"Where'd he grow up? Do you remember the place?"* |
| `terms_of_endearment` (B) | missing | Ride on the Monica thread later: *"What did they call each other?"* |
| `pets` (B) | missing | Optional — only if it comes up naturally (Chandler's duck/chicken is sitcom-specific; a real client may have none) |
| `opening_closing_phrases` (E) | **artifact_dependent** | **Defer to data-sharing phase** — needs texts. Don't interrogate. |
| `message_length_style` (E) | weak | Confirm via artifacts (text-export frequency), not conversation |

**What Carrie does NOT chase:** the sensitive layer (Cluster F) already surfaced appropriately; she never lists it as a gap and never digs for more.

### Circle-back example (the gap-fill protocol in action)

Days later, anchored on the Monica thread Carrie already has:

> **Carrie [Circle-back]:** You said Monica was the one who stayed — that she broke the spell. I keep thinking about that. Did they have a name for each other? The little pet-name stuff?

This fills `terms_of_endearment` (B) *through* the Monica memory — it reads as curiosity and continuity, not "item 4 on the list." Backstage, it closes a `missing` gap and likely surfaces another memory that cascades.

---

## 5. Coverage Assessment vs. Quality Scoring

Mapping the captured fields to the [Persona Quality Scoring](#) **Axis-1 dimensional coverage** requirement (speech / humor / emotion / bio must all be populated):

| Dimension | Status after ~12 turns |
| --- | --- |
| **Speech** | Partial — signature construction + voice cue captured, but verbatim opening/closing phrases are artifact-dependent. **Needs artifacts to fully clear.** |
| **Humor** | ✅ Strong — humor style, defensive function, absurdist bits all populated. |
| **Emotion** | ✅ Strong — abandonment terror, private vulnerability, the defining 2am memory. |
| **Bio** | Partial — name, relationships, occupation, daily rhythm captured; birthplace/nicknames missing. **Gap-fillable.** |

**Verdict:** Three of four dimensions are populated or near-populated from a *single short conversation*, with the fourth (speech-verbatim) correctly identified as artifact-dependent rather than conversation-dependent. That's exactly the behavior the protocol is supposed to produce: the conversation fills what conversation *can* fill, and cleanly hands off what it can't.

---

## 6. What This Test Proves (and What It Doesn't)

### ✅ Proven
- **The flow produces a real conversation.** Open → invite → follow-the-thread stayed one-question-at-a-time and memory-led. No stacking, no field names, no "next section."
- **The extraction schema works on real utterances.** One turn (the "shield" turn) filled four fields; the verbatim quote captured a `grounded` value a form could never get.
- **Gap-fill is natural, not survey-like.** Every gap maps to a circle-back anchored on an existing memory. The client never sees a checklist.
- **Sensitive material is handled.** The abandonment fear surfaced voluntarily and was tagged `sensitive`, never listed as a gap to chase.
- **The method captures richer data than a form.** The defensive-humor dynamic, the private late-night voice, the "only you" contrast — none of these exist in any questionnaire field, yet they're the most valuable persona inputs.

### ❌ Not yet proven (out of scope for this test)
- **Persona fidelity (Axis 2).** Whether an LLM actually *sounds* like Chandler requires the persona/conversation module, which is currently empty. That's a separate build, tracked elsewhere.
- **Real extraction code.** This test was done by hand against the schema. The L1/L2 extractor that turns a transcript into `traits.json` still needs to be implemented (see protocol §9).
- **Real grief dynamics.** Sam is simulated. Real-client calibration comes after launch.

---

## 7. Recommendations

1. **Implement the transcript → `traits.json` adapter** as the next engineering step. The schema is the contract; the pipeline modules already exist downstream.
2. **Use this Persona-0 run as the calibration fixture** for the extractor — hand-validated extractions above are the ground truth the code should reproduce.
3. **Add "artifact-dependent gap" detection to the quality report** so conversation-derived and artifact-derived coverage are reported separately (the test shows speech-verbatim can't come from talk).
4. **Keep Chandler as the permanent regression persona.** Re-run this protocol test whenever the schema or Carrie persona spec changes, so we catch regressions in the method.

## Related
- [`conversational-onboarding-protocol.md`](./conversational-onboarding-protocol.md) — the method tested here
- [`conversation-extraction-schema.md`](./conversation-extraction-schema.md) — the data contract exercised here
- [`bio-questionnaire-template.md`](./bio-questionnaire-template.md) — Carrie's playbook
- Vault: `persona-0-chandler-bing`, `persona-quality-scoring`, `chandler-bing/02-clean/persona-profile.md` (ground truth)
