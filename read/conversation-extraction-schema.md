# Conversational Onboarding — Data Extraction Schema

> **Audience:** Onboarding Agent (Carrie's backstage layer), R&D, engineering.
> **Client visibility:** **None.** The client never sees fields, clusters, or this schema. It is the silent mapping that turns Carrie's conversation into the puzzle — the structured data R&D requires — without the client ever touching a form.
> **Pairs with:** [`conversational-onboarding-protocol.md`](./conversational-onboarding-protocol.md) (the method), [`bio-questionnaire-template.md`](./bio-questionnaire-template.md) (Carrie's playbook).
> **Anchored to:** `extract.py` / `clean.py` / `stats.py` → `traits.json` / `gaps.md` pipeline and the two-axis [Persona Quality Scoring System](#).

---

## 1. Why This Schema Exists

The shift from questionnaire to conversation solves the *experience* problem. It creates a new *engineering* problem: **the data no longer arrives pre-structured.** A form hands you `hobbies: baseball`. A conversation hands you *"he broke his arm in 4th grade so he couldn't play anymore, but he watched every single Cubs game even when they were terrible."*

That sentence is gold — it fills nine puzzle fields at once. But only if there is a deterministic way to **detect** what's in it, **map** it to the right fields, **rate** how confident we are, and **flag** what's still missing. That is what this schema is. It is the contract between Carrie's conversation and the backstage pipeline.

Three rules govern everything below:

1. **The conversation is the source of truth.** Structured fields are *derived* from utterances, never a replacement for them. We always keep the client's exact words linked to every extracted value (citation).
2. **Memories beat facts.** A bare fact (`"he liked baseball"`) is low-value, low-confidence data. The memory around it is high-value. The schema rewards the memory and flags the bare fact as "needs the story."
3. **Inferences are labeled, never stated as fact.** If we infer "Chicago ties" from "the Cubs," it is stored as an *inference* to confirm gently later — never as a grounded fact.

---

## 2. The Two Layers

Extraction happens in two layers that mirror the existing pipeline:

| Layer | What it does | How | Confidence |
| --- | --- | --- | --- |
| **L1 — Deterministic** | Captures verbatim tokens the client said: exact phrases, names, signature words. No LLM judgment. | Pattern/keyword match on the transcript + `stats.py` style frequency counting. | **Grounded** (the client said these exact words) |
| **L2 — Inferred** | Maps an utterance to abstract puzzle fields and reads between the lines (personality, values, emotional dynamics). | Grounded LLM extraction (strict prompt — see playbook §8) over the transcript, citing the source span. | **Inferred** (our interpretation) unless the client stated it directly |

Every extracted record carries a `confidence` of `grounded`, `inferred`, or `weak`, plus a `source` citation back to the exact utterance. This is what lets us later tell the difference between *"Carrie, what's his team?" "The Cubs"* (grounded) and *"he must have Chicago ties"* (inferred from the Cubs).

---

## 3. The Puzzle Fields — Full Schema

These are the data points R&D requires (the "100+ puzzle pieces"). They are grouped into **clusters** that match the clusters Carrie carries in her head (see playbook §6). Each field below specifies: what it is, the **detection signals** that tell the extractor it's present in an utterance, and example extraction.

Notation:
- **Signal** = the linguistic cue the extractor keys on. Signals are *memory-shaped*, not field-named — we detect "a passion with a story" rather than scanning for the word "hobby."
- **L** = which layer captures it (L1 deterministic / L2 inferred).
- **Conf** = default confidence if captured only from conversation (upgradeable when artifacts like texts/voice arrive).

### Cluster A — Identity & Their World

| Field | What | Detection signals (memory-shaped) | L | Conf |
| --- | --- | --- | --- | --- |
| `name` | The person's name | Self-introduction by the client; Carrie asks "what did people call them" | L1 | grounded |
| `nicknames` | Names only certain people used | "only family called him…", "her friends knew her as…" | L1 | grounded |
| `birthplace_region` | Where they're from / region ties | A place attached to a story ("grew up in Naples", inferred from team/loyalty) | L1/L2 | grounded→inferred |
| `era_of_life` | Life stage / era anchors | Time markers tied to events ("in 4th grade", "after the army", "when the kids were little") | L2 | inferred |
| `occupation_work` | What they did | Work attached to identity or an anecdote, not a bare job title | L1/L2 | grounded |
| `languages` | Languages spoken | Direct mention; or accent/dialect cues | L1/L2 | grounded→inferred |
| `accent_voice_cues` | How they pronounced things | "she said it like…", a word they "always reached for", a quoted pronunciation | L1 | grounded |
| `daily_rhythm` | The shape of their day | Ritual/routine memories ("every morning he…", "her spot on the couch") | L2 | inferred |

### Cluster B — The People Who Mattered

| Field | What | Detection signals | L | Conf |
| --- | --- | --- | --- | --- |
| `relationships` | People in their world + nature of bond | A person named *with a dynamic*, not a bare label ("Maria was the one who called him out — he secretly loved it") | L1/L2 | grounded→inferred |
| `terms_of_endearment` | What they called each other | Direct quote of the pet name; "he called me…" | L1 | grounded |
| `complicated_relationships` | Tense/complex bonds | Hedging, ambivalence, "it was… complicated", a short answer with weight | L2 | inferred |
| `pets` | Animals in their world + bond | Pet named with personality ("Bruno, a big dopey mastiff") | L1/L2 | grounded |
| `social_style` | How they related to people generally | "would talk to anyone", "kept a small circle", loyalty-through-losing-seasons | L2 | inferred |

### Cluster C — Who They Were (Personality)

| Field | What | Detection signals | L | Conf |
| --- | --- | --- | --- | --- |
| `personality_traits` | Core traits | A trait *shown through a story*, not asserted ("stubborn in the best way" backed by an anecdote) | L2 | inferred |
| `humor_style` | Kind of humor + what made them laugh | "the whole room laughed" vs "between us"; a specific bit they did | L2 | inferred |
| `values` | What they cared about | What they spent time/money on, what they refused, what they stood for | L2 | inferred |
| `temper_under_pressure` | How they carried hardship | "when life got hard…", how they handled loss/limitation | L2 | inferred |
| `quirks_habits` | Recurring small behaviors | "he always…", a catchphrase, a fidget, a ritual | L1/L2 | grounded→inferred |
| `catchphrases` | Signature words/phrases | Repeated verbatim; "she always said…" | L1 | grounded |
| `surprises` | Counterintuitive things about them | "you'd never guess but…", a contrast | L2 | inferred |

### Cluster D — Moments & Memories

| Field | What | Detection signals | L | Conf |
| --- | --- | --- | --- | --- |
| `defining_memory` | A memory that *is* them | "the thing I keep thinking about", a story told with energy | L2 | inferred |
| `loved_stories` | Stories they told repeatedly | "he'd tell this story every…", "more than once" | L2 | inferred |
| `places_traditions` | A place/tradition that mattered | A location tied to feeling; a repeated occasion | L2 | inferred |
| `hard_memory` | A painful memory | Hesitation, weight, emotional shift — handle per Cluster F | L2 | inferred |
| `words_that_stuck` | Something they said the client never forgot | Direct quote: "he once told me…" | L1 | grounded |

### Cluster E — How They Talked (Voice)

> **Highest-fidelity cluster for persona authenticity.** Always preserve the client's *exact phrasing* here — the persona needs their real words, not a summary.

| Field | What | Detection signals | L | Conf |
| --- | --- | --- | --- | --- |
| `opening_closing_phrases` | How they started/ended exchanges | "the 'hey kiddo' stuff", sign-offs, greetings quoted verbatim | L1 | grounded |
| `message_length_style` | Short/long, playful/serious | "her texts were always…", contrast between them and others | L2 | inferred |
| `signature_words` | Words that were just theirs | A word the client flags as theirs; high-frequency word in artifacts | L1 | grounded |
| `inside_jokes` | Shared humor only they got | "between us", "you had to be there", a recurring bit | L2 | inferred |
| `private_voice` | The "only you" version of them | Contrast probe: "did they talk to you differently than to everyone else?" | L2 | inferred |

### Cluster F — The Sensitive Layer (Special Handling)

| Field | What | Detection signals | Handling |
| --- | --- | --- | --- |
| `health` | Physical/mental health | Surfaces on its own; never prompted directly | **Encrypt at rest.** Pipeline-only access. Never used for marketing. Delete after extraction on request. |
| `private_struggles` | Addiction, legal, deeply personal | Hedging, long pauses, "this is hard to talk about" | Same as `health`. Treat as sacred. |
| `beliefs_faith` | Religion, spirituality, philosophy | Mentioned in connection to meaning/ritual | Standard protection; mark sensitivity flag. |

> **Sensitive-layer rule:** when one of these surfaces, Carrie acknowledges it as a gift, never digs for more. The extractor tags it `sensitive: true` and it flows into the protected handling path defined in the [data privacy policy](#). It is never a gap to chase.

---

## 4. Confidence & Citation Model

Every extracted value is stored as a record, not a bare string:

```json
{
  "field": "humor_style",
  "cluster": "C",
  "value": "Shared, character-driven — he narrated the dog's thoughts in a ridiculous Italian accent",
  "confidence": "inferred",
  "layer": "L2",
  "source": { "utterance_id": "turn-014", "quote": "He had this voice he'd do for our dog… a ridiculous Italian accent" },
  "sensitive": false,
  "inferences": ["italian_heritage"],
  "needs_confirmation": false
}
```

Confidence levels:

- **`grounded`** — the client stated this in their own words (exact quote captured). The strongest evidence. Upgrades automatically when a matching artifact (text/voice) corroborates it.
- **`inferred`** — we read it from the memory (e.g., loyalty from "watched every game even when they were terrible"). Valid for persona-building; flagged for gentle confirmation if it's load-bearing.
- **`weak`** — a bare fact with no story ("he liked baseball") or a thin inference. **Always treated as a gap to deepen**, never as a completed field.

**The bare-fact rule:** if the extractor captures a Cluster C/D/E field at only `weak` confidence (a fact without its memory), Carrie's next natural follow-up targets *the story behind the fact*. This is the single most important quality lever in the system — it's what separates a rich persona from a flat one.

---

## 5. How Extraction Maps to the Existing Pipeline

This schema is the **input contract** for the backstage pipeline. The conversation transcript arrives as the canonical record; extraction produces the structured layers the rest of the pipeline already expects.

```
Conversation transcript (canonical, the client's exact words)
        │
        ▼
┌─────────────────────────────────────────────┐
│ L1 Deterministic capture                    │
│  • verbatim names, signature words, quotes  │
│  • frequency / n-gram stats (stats.py)      │
│  → grounded records + stats.json            │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│ L2 Grounded LLM extraction (strict prompt)  │
│  • maps utterances → puzzle fields          │
│  • labels confidence + inferences           │
│  • cites source span on every value         │
│  → traits.json (all records, clustered)     │
└─────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│ Gap detection                               │
│  • fields absent OR confidence == weak      │
│  • sensitive fields never listed as gaps    │
│  → gaps.md (Carrie's follow-up guide)       │
└─────────────────────────────────────────────┘
        │
        ▼
   quality-report.md  →  Axis 1 (Data Quality) of the scoring system
```

**Alignment with the two-axis scoring model:**
- The clusters map to the **dimensional coverage** dimension (speech / humor / emotion / bio must all be populated). Cluster E feeds *speech*; C `humor_style` feeds *humor*; D + the emotional texture of memories feed *emotion*; A/B feed *bio*.
- `weak`-confidence fields do **not** count as populated coverage — they count as gaps. This prevents a transcript full of bare facts from passing the dimensional-coverage bar.
- `grounded` records with corroborating artifacts raise the **vocabulary richness** and **cleanliness** scores; inferred-only records do not.

---

## 6. Gap Taxonomy — Not All Gaps Are Equal

`gaps.md` is Carrie's follow-up map. Gaps are classified so Carrie knows how hard to push (or whether to push at all):

| Gap type | Meaning | Carrie's move |
| --- | --- | --- |
| **`shallow`** | A `weak` fact — the field has data but no memory | Natural follow-up: *"How'd that start? Was there a moment?"* |
| **`missing`** | The field has nothing yet | Wait for a natural opening; never ask cold. Memory-led pivot. |
| **`sensitive`** | Falls in Cluster F and hasn't surfaced | **Never chase.** It surfaces when trust allows. Not listed to the client. |
| **`inferred_unconfirmed`** | We made a load-bearing inference (e.g., region from a team) | Gentle confirmation later: *"Was his family from around there?"* |
| **`artifact_dependent`** | Needs texts/voice/photos, not conversation (e.g., exact sign-off words) | Defer to the data-sharing phase, not the conversation. |

Carrie never shows this taxonomy to the client. She experiences it as "things I'm curious about, for later." The protocol document defines *how* she turns each gap type into a natural moment.

---

## 7. Worked Detection Example

Client utterance (one turn):

> *"Honestly the thing I keep thinking about is baseball. He broke his arm in 4th grade, couldn't play anymore. But he was such a fan — the Cubs, diehard. He'd watch every single game, even when they were terrible. He'd make these awful nachos and yell at the TV. Every time."*

What the extractor produces from this **single turn**:

| Field | Value (preserving client voice) | Conf | Source |
| --- | --- | --- | --- |
| `defining_memory` | baseball + the broken arm — "the thing I keep thinking about" | inferred | turn-001 |
| `era_of_life` | 4th grade (childhood anchor) | inferred | turn-001 |
| `personality_traits` | loyal, devoted (stayed a fan after he couldn't play) | inferred | turn-001 |
| `temper_under_pressure` | humor + perseverance through loss/limitation | inferred | turn-001 |
| `quirks_habits` | game-day ritual — awful nachos, yelled at the TV, every time | inferred | turn-001 |
| `humor_style` | expressive, funny, loud | inferred | turn-001 |
| `places_traditions` | the game-day ritual/tradition | inferred | turn-001 |
| `signature_words` | "diehard" (the client's word — keep verbatim) | grounded | turn-001 |
| *(inference)* `birthplace_region` | possible Chicago ties (from "the Cubs") → **inferred_unconfirmed gap** | inferred | turn-001 |

**One turn → eight grounded/inferred fields + one inference to confirm later.** A form would have captured three blanks ("school", "hobbies", "teams") and stopped. The conversational method, with this schema, captured everything *plus* the nachos.

---

## 8. Versioning & Calibration

- This schema is **v1**, aligned to the proposed v1 quality-scoring thresholds. Field set and confidence rules will be revised as real builds calibrate the model (target: after ~25 builds, per the scoring system's calibration plan).
- Every completed onboarding records which fields landed at which confidence, building the calibration dataset that turns `inferred` thresholds into empirical bars.
- When the field set changes, bump the version and re-process the Chandler Persona-0 reference run for comparability.

## Related

- [`conversational-onboarding-protocol.md`](./conversational-onboarding-protocol.md) — the method (how Carrie runs the conversation this schema captures)
- [`bio-questionnaire-template.md`](./bio-questionnaire-template.md) — Carrie's playbook (the human-facing layer of this schema)
- [`chandler-bing-persona0-protocol-test.md`](./chandler-bing-persona0-protocol-test.md) — this schema exercised end-to-end on Persona-0
- Persona Quality Scoring System (vault) — the two-axis model this schema feeds
- `onboarding-agent-playbook` (vault) — the extract/clean/stats/traits/gaps pipeline this schema is the input contract for
