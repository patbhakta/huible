# Onboarding Data Requirements — Audit & Minimum Viable Puzzle

> **Purpose:** Answer the question Pat put to onboarding: *what does Carrie ACTUALLY need to know to build a believable persona?* Not a complete biography — just enough truth to make the person feel real.
> **Audience:** Onboarding Agent, Carrie persona spec, R&D, product.
> **Pairs with:** [`bio-questionnaire-template.md`](./bio-questionnaire-template.md) (Carrie's playbook), [`conversational-onboarding-protocol.md`](./conversational-onboarding-protocol.md) (the method), [`conversation-extraction-schema.md`](./conversation-extraction-schema.md) (the data contract), [`grief-aware-onboarding-guide.md`](./grief-aware-onboarding-guide.md) (clinical layer).
> **Source under review:** the original 32-item form-style questionnaire (`85c2c55`), now retired in favor of Carrie's conversation.

---

## 1. The Verdict in One Sentence

Carrie needs **memories that reveal personality, the voice that makes the person sound right, and the relationships that anchor both** — everything else is either a side-effect of those three, inferable from artifacts, or resume-style biodata we no longer collect for its own sake.

The baseball-arm principle is the rule: *"he went to 4th grade"* is dead; *"he broke his arm in 4th grade so he couldn't play baseball anymore, but he stayed a diehard fan"* is alive. The first fills a field; the second fills nine fields *and* seeds the next question.

---

## 2. Cut vs Keep — Audit of the Original 32-Item Form

The original form (`85c2c55`) had five sections, 32 questions, marked Optional/Required. We audited each item against one test: **does this, by itself, make the persona more believable?** Items that only anchor identity without revealing personhood are **CUT as first-class asks** (still captured as side-effects of memory, never requested for their own sake). Items that reveal personality, voice, or relationship texture are **KEPT** and reframed as memory prompts.

### 2.1 CUT — "resume-style biodata" (do not ask; capture only as a side-effect)

These do not make a person feel real. A persona built on them sounds like a LinkedIn summary. Carrie never asks for them directly; if they fall out of a memory, fine.

| Original item | Section | Why it's cut |
| --- | --- | --- |
| Birth date / date of passing (#2) | Basic Info | **Death facts are a re-traumatization vector** (Clinical Advisor §2.2 — never ask about the death). Birth date is identity plumbing, not personality. Era-of-life anchors come from memories ("when the kids were little"), not dates. |
| Where born / where lived (#3) | Basic Info | Region ties are *inferable* from loyalty/affiliation ("the Cubs" → Chicago). Asking "where were they born" is intake language. |
| Full legal name (#1, partial) | Basic Info | The name they went by is what Carrie needs ("what did people call them?"). Legal name is plumbing. |
| Occupation / "best known for" (#4) | Basic Info | A bare job title is dead data. Chandler's "nobody ever knew what he did" is the *memory*; "statistical analysis" is the boring version. Capture work only when it's attached to a story. |
| Languages spoken (#5) | Basic Info | Inferable from accent/heritage cues. Never a standalone question. |
| Daily routine (#6) | Basic Info | Only valuable as a *ritual memory* ("every morning he…"), not a schedule. |
| Hobbies/interests (#7) | Basic Info | **The canonical "dead fact."** "Hobbies: baseball" is worthless; "broke his arm, watched every Cubs game" is gold. Reach for the story, never the list. |
| Immediate family roster (#8) | Relationships | A names list is a contact sheet. Capture people *with dynamics*, never as a roster. |
| Five words to describe them (#13) | Personality | Asserted adjectives are the lowest-value personality data. "Funny" means nothing; "the funny was a shield" is everything. |
| Emoji/slang/formal language usage (#27) | Comm. Style | Artifact-dependent — must come from texts, not conversation. Asking it yields self-report, which is unreliable. |

**What "cut" means concretely:** Carrie never opens with these, never circles back to them, and never lists them as gaps. If the extraction schema already has a `grounded` value for one (it fell out of a memory), great. If not, it is **not** a gap worth filling — it does not block the build.

### 2.2 KEPT — "personality-revealing memories" (the load-bearing asks)

These are the asks that make a persona *feel* right. Each is reframed from a form field into a memory prompt Carrie carries (see playbook §6 clusters).

| Original item | Reframed as (Carrie's memory door) | Cluster |
| --- | --- | --- |
| Nicknames / family-only names (#1) | "What did people call them? Was there a name only family used?" | A |
| Pet names, what you called each other (#10) | "What did they call you? What did you call them?" | B |
| Sense of humor / what made them laugh (#14) | "What made them laugh — like, actually laugh?" | C |
| How they handled stress/hard times (#16) | "When life got hard — how'd they carry it?" | C |
| Quirks, habits, catchphrases (#17) | "Any little thing they always did? A catchphrase?" | C/E |
| Values/beliefs (#18) | Surfaced through what they spent time on / refused / stood for | C |
| Something that would surprise people (#19) | "You'd never guess but…" — the contrast | C |
| A favorite memory (#20) | "What's a memory that always makes you smile?" | D |
| A story they loved to tell (#21) | "A story they loved to tell — maybe more than once?" | D |
| A moment that captured them (#22) | "A moment that, to you, is just *them*?" | D |
| Places/traditions (#23) | Surfaces through sensory drill-down ("was there a spot he always sat?") | D |
| Words that stuck (#25) | "Something they once said you never forgot?" | D/E |
| How they opened/closed conversations (#26) | "How did they usually open or close? The 'hey kiddo' stuff." | E |
| Common phrases/signature words (#29) | "Any phrase that was just *theirs*?" | E |
| Tone shift with different people (#30) | "Did they talk to you differently than to others?" — the "only you" version | E |
| Inside jokes (#32) | Surfaces through shared-story memories | E |
| Complicated relationships (#11) | Only if it comes up; "you can keep that as short as you want" | B/F |
| Pets (#12) | "Any animals in their world?" — only with personality attached | B |
| Bittersweet/difficult memory (#24) | **Opt-in, content-warned, never chased** — Clinical Advisor §2 | D/F |

Notice the pattern: **every KEPT item is a door, not a field.** "What makes them *them*" is the door; `personality_traits` is the field the extractor fills behind it. The client never sees the field name.

### 2.3 Artifact-dependent — conversation cannot fill these

| Item | Why talk can't get it | Routed to |
| --- | --- | --- |
| Exact opening/closing words (#26, verbatim) | Self-report is unreliable; need real messages | Data-sharing phase (texts) |
| Message length/style, emoji frequency (#27, #28) | Must be measured, not asked | Data-sharing phase (texts) |
| Writing-vs-speaking difference (#31) | Needs both modalities as artifacts | Data-sharing phase (texts + audio) |

These are flagged `artifact_dependent` in the extraction schema and deferred to the data-sharing phase. Carrie does not interrogate for them; she hands off warmly (protocol §5.4).

---

## 3. The Minimum Viable Puzzle

If Carrie had to build a believable persona from the *fewest* possible captures, these are the load-bearing fields. Everything else is enrichment. This is the answer to the Key Question.

| Tier | What | Why load-bearing | How Carrie reaches it |
| --- | --- | --- | --- |
| **Must-have** | `name` + `relationship` to client | You cannot introduce or refer to the person without it | Falls out of the opening exchange |
| **Must-have** | One `defining_memory` / `words_that_stuck` (verbatim) | A single grounded quote ("hopeless and awkward and desperate for love") is the highest-value capture — it carries voice, personality, and emotion at once | The story-behind-the-fact follow-up |
| **Must-have** | `humor_style` + one `quirk_habit`/`catchphrase` | How they're funny and one repeated thing they did is what makes them *them* | Cluster C invitations + sensory drill-down |
| **Must-have** | `relationships` with dynamics (≥1) | A persona with no people to reference is a monologue | Cluster B invitation |
| **High-value** | `private_voice` / the "only you" version (contrast) | The private version is the most valuable persona data — no form captures it | The contrast probe |
| **High-value** | `accent_voice_cues` + `signature_words` | Makes the persona *sound* right, even before artifacts arrive | Sensory drill-down on how they talked |
| **Enrichment** | `places_traditions`, `loved_stories`, `daily_rhythm`, `values`, `temper_under_pressure` | Deepen realism; each cascades from a memory | Thread-following, circle-backs |
| **Never chased** | Cluster F (private struggles, the death) | Surfaces voluntarily when trust allows; never a gap | Clinical Advisor §2 — honor the boundary |

**The build rule:** if the Must-have tier is populated at non-`weak` confidence, the persona is buildable to Standard dimensional coverage for **emotion** and **personality**. **Speech/voice** clears as far as conversation can take it and then defers to artifacts. **Bio** is the dimension most tolerant of gaps — birthplace and dates do not make a persona more real; memories do.

This is why the Chandler Persona-0 test cleared three of four quality dimensions from a ~12-turn conversation without a single form field.

---

## 4. Clinical Advisor Integration — Proof the Recommendations Are Wired In

Issue asks: integrate the grief-counseling techniques from the Clinical Advisor (`grief-aware-onboarding-guide.md`, [ff31475d](/ad9/issues/ff31475d-4e39-403f-8426-9b127ed61eaa)) into the conversation flow. The integration is substantive, not a link. Map:

| Clinical Advisor recommendation | Where Carrie's protocol operationalizes it |
| --- | --- |
| **Never ask about the death** (§2.2) | Audit CUTS birth-date/passing-date; protocol §5.5 + Cluster F: sensitive material is never listed as a gap, never chased |
| **Everything optional, no deadlines, no urgency** (§2) | Protocol §1 Law 1 (client's pace, always); §7 "additive, never overdue"; Carrie anti-patterns: "never imply a deadline" |
| **Loss of agency = re-traumatization** (§2.1) | Protocol §5.1 "gaps are Carrie's, never the client's" — the client never sees a checklist, never hears "we still need X" |
| **Cooling-off / tolerate oscillation** (§1.2, §6) | Protocol §7 "gentle, guilt-free check-ins, then space. Never chase."; §4 "Silent → one gentle check-in after a few days" |
| **Warm, non-stigmatizing referral; door stays open** (§3.2) | Protocol §4 "Raw and grieving → more exits than invitations"; §2.4 "offer a pause and make it real" |
| **Harmful vs healing language** (§5) | Protocol §2.2 Carrie's voice: plain, warm, mirrors the client's words; §2.4 "never defend, never redirect, never cheerlead"; §2.6 "never sound like a brand, a bot, or a counselor" |
| **Disclosure boundary ("reflection, not the person")** (§5.3) | Carrie never implies she *is* the deceased (she is the caretaker); the boundary is reinforced at the pre-conversation handoff, not undermined during intake |
| **Acknowledge emotion before anything else** (§4.2) | Protocol §2.4 step 1: "Acknowledge first, before anything else"; "a correct answer delivered coldly is worse than no answer" |
| **Acute-grief cognitive overload → one thing at a time** (§1.3) | Protocol §3.2 "the unit of pacing is one move per message"; Law 1 "one question at a time" |

The clinical layer is the *why* behind Carrie's behavior; the protocol is the *how*. Both ship together.

---

## 5. What Changed vs the Original Intake

| | Original form (`85c2c55`) | Conversational protocol + this audit |
| --- | --- | --- |
| Client's job | Fill 32 fields | Talk about someone they love |
| Data collected | Facts ("Hobbies: baseball") | Memories ("broke his arm, watched every Cubs game") |
| Biodata items | Asked directly as Required/Optional | CUT — captured only as side-effects of memory |
| The death | Date-of-passing is field #2 | Never asked (Clinical Advisor §2.2) |
| Voice/verbatim | Self-report questions | Artifact-dependent; deferred to data-sharing |
| Structuring | Client structures into fields | Carrie + extractor structure backstage |
| Minimum viable | Implicitly "all 32" | Explicit 4 Must-haves (this audit §3) |
| Feel | Insurance paperwork | Being heard by a person |

---

## 6. Open Follow-ups (not blocking this issue)

- [ ] Implement the transcript → `traits.json` adapter so the L1/L2 extractor turns a real Carrie conversation into the puzzle (protocol §9, schema §2). Engineering.
- [ ] Calibrate circle-back timing and the "rich enough to stop active gap-fill" threshold against real onboardings after launch.
- [ ] Wire Carrie's persona spec (protocol §2) into the actual caretaker agent prompt when the conversation module is built.

## Related

- [`conversational-onboarding-protocol.md`](./conversational-onboarding-protocol.md) — the method
- [`conversation-extraction-schema.md`](./conversation-extraction-schema.md) — the puzzle-field contract
- [`bio-questionnaire-template.md`](./bio-questionnaire-template.md) — Carrie's playbook (the human-facing layer)
- [`grief-aware-onboarding-guide.md`](./grief-aware-onboarding-guide.md) — the clinical layer
- [`chandler-bing-persona0-protocol-test.md`](./chandler-bing-persona0-protocol-test.md) — the method validated end-to-end on Persona-0
- Clinical Advisor consultation: [ff31475d](/ad9/issues/ff31475d-4e39-403f-8426-9b127ed61eaa)
