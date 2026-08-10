# Onboarding Redesign — Progressive Disclosure Research

> **Goal:** Turn the current "wall of questions" intake into a conversation that unfolds naturally.
> **Scope:** Research + concrete flow recommendations for Huible's client-facing onboarding.
> **Status:** R&D deliverable. Source materials reviewed: `client-welcome-message.md`, `bio-questionnaire-template.md`, `flows/onboard.yaml`.

---

## TL;DR — The Core Shift

The current onboarding is **a beautiful form.** Every section, every sub-question, every "if you'd like to share more" is visible from the first second. The tone is gentle, but the *structure* is paperwork: six sections, ~24 required prompts, ~12 optional ones, all presented at once. A grieving person opens it and sees the whole mountain they have to climb before they get anything back.

**The redesign replaces the form with a conversation that earns each next question.** Start with almost nothing — a name and one memory. Give something meaningful back immediately. Then, over days, unfold one warm, single question at a time, each one rooted in what the person already shared. Data becomes a *byproduct* of reminiscing, never the stated goal.

This is **progressive disclosure applied to emotional onboarding** — the same pattern that makes a print dialog show three options first and hide "advanced settings" behind a click, but mapped onto trust, pacing, and grief.

---

## 1. What Grief Counseling Intake Actually Does

The closest professional analogue to what Huible is doing is grief counseling and bereavement support. The most striking finding from that field: **intake is never a questionnaire.** Real grief work treats the first conversation as part of the healing, not paperwork to clear before the healing starts.

**Key frameworks and what they imply for Huible:**

- **Wolfelt's "Companioning" model (vs. "treating").** Dr. Alan Wolfelt argues the grief supporter's job is not to *assess and fix* but to *walk alongside*. His principles include "companioning is about being present to another person's pain; not taking it away." The practical consequence: you never lead with logistics. You lead with presence. The first thing a counselor does is *sit with* the bereaved and let them set the pace. → **Huible's first screen should ask for almost nothing and signal "I'm here, on your time."**

- **The Dougy Center model (grieving children/families).** The Dougy Center — the leading U.S. center for grieving children — structures sessions around voluntary participation and "honoring the person who died" through stories and objects, never through forms. Participation is always opt-in; children share when moved. → **Make every share voluntary and framed as honoring them, not feeding a database.**

- **Worden's Four Tasks of Mourning.** J. William Worden frames grief as active tasks the mourner moves through (accept the reality, process the pain, adjust to a world without them, find an enduring connection). The key insight: a person is *in the middle of* these tasks when they arrive at Huible. Forcing a comprehensive intake front-loads emotional labor they may not be ready for. → **Spread emotional asks across time; let the griever decide when they're ready for each.**

- **Stroebe & Schut's Dual Process Model.** Grieving people oscillate between *loss-oriented* (confronting the loss, reminiscing, crying) and *restoration-oriented* (doing life, distraction, logistics) states. A person might be ready to remember deeply on Tuesday and need a break on Wednesday. → **The onboarding must tolerate stop-and-start; never penalize someone for stepping away, and never require a single sitting.**

- **Continuing Bonds theory (Klass, Silverman, Nickman).** Modern grief research has moved away from "let go and move on" toward *maintaining a healthy ongoing relationship* with the deceased. This is *literally Huible's product*. The onboarding is the opening of that continuing bond — so it should feel like the beginning of a relationship, not the closeout of an estate. → **Frame every step as "staying close," and never as "completing a file."**

**The transferable principle:** *Pacing follows the griever, not the form.* A counselor would never hand a newly bereaved person a 6-section intake packet. They'd ask, gently, "Tell me about them." That single invitation is the entire intake.

---

## 2. Progressive Disclosure — The Design Pattern, Mapped to Emotion

Progressive disclosure is one of the oldest, best-validated patterns in interaction design. Nielsen Norman Group defines it simply:

> "1. Initially, show users only a few of the most important options. 2. Offer a larger set of specialized options upon request, disclosing secondary features only if a user asks for them."

NN/g stresses two things you must get right, and both translate directly to Huible:

1. **The right split between initial and secondary.** "You have to disclose everything that users frequently need up front… conversely, the primary list can't contain too many options or you'll fail to sufficiently focus attention." For Huible: the *only* things needed up front are a name and *one* entry point to a memory. Everything else is secondary and should be deferred.

2. **It must be obvious how to progress.** "Label the button in a way that sets clear expectations for what users will find at the next level." For Huible: the path from "I shared one memory" → "here's something back" → "whenever you're ready, here's a warm next question" must feel obvious and inviting, never like turning a page in a test.

NN/g also distinguishes **staged disclosure** (a wizard — linear, one step at a time, each step simple) from progressive disclosure (hierarchical, core-first). The current Huible questionnaire accidentally does *neither*: it's a single screen that dumps every step at once. The redesign wants a **hybrid**: staged for the emotional cadence (one question, then the next, over time) with progressive layering (each share reveals a slightly deeper optional layer).

A critical NN/g warning: *"Designs that go beyond 2 disclosure levels typically have low usability."* The current questionnaire already has at least 3 levels (section → prompt → "if you'd like to share more"), all visible at once. The redesign should keep visible depth to **one level** at any moment.

**The emotional version of the pattern has an extra rule the UI version doesn't:** *each disclosed layer must give something back before asking for more.* In a print dialog, revealing "advanced settings" costs the user nothing but a click. In grief onboarding, each new ask costs emotional energy — so it must be *earned* by a prior moment of feeling heard or seeing something meaningful return. Disclosure becomes a reciprocal loop, not a one-way extraction.

---

## 3. Making Data Collection Feel Like Reminiscing

This is the heart of the redesign. The current questionnaire tries hard with warm language ("Write the way you actually talk," "nothing you share is too small"), but it is still **a questionnaire**: sectioned, exhaustive, asking the griever to *produce* answers on demand. Reminiscing is the opposite — it's involuntary, associative, triggered, and pleasurable.

**Techniques that convert extraction into reminiscing:**

- **Story elicitation, not field completion.** Replace "What did they do for work? What were they most proud of?" with a single open invitation: *"Tell me about a time you saw them light up."* A story contains the work, the pride, the humor, the values — all captured as a byproduct. The oral-history field (e.g., StoryCorps's "Great Questions" methodology) has proven this for decades: one good question yields richer material than ten form fields.

- **Use artifacts as prompts, not the questionnaire.** A photo, a saved voicemail, a song lyric triggers memory far more powerfully than a written prompt. The deepest data (voice patterns, word choice) comes from *their* artifacts, not from the griever's prose about them. Invite artifacts as the primary input and let the questionnaire fill gaps.

- **One thing at a time.** Reminiscing can't be batched into six sections. One question, fully attended to, beats six sections skimmed. Spacing also matches the Dual Process Model — the griever dips into loss-oriented memory, then returns to life, then dips again.

- **Reflect before you ask again.** After every share, the system should reflect back what it heard — "He sounds hilarious — that story about the porch. I can almost hear him." This does three things: (1) proves the material was received and cared for, (2) the act of *summarizing* is itself data capture the griever can correct, (3) it builds the relational trust that *earns* the next question.

- **Let them correct you.** "That's not quite right — he'd never say 'splendid'" is gold. Each correction is high-signal data the griever *wants* to give, because it protects the person's memory. This is the same dynamic the welcome message already names ("every 'she wouldn't say that'… makes it more like them") — surface it *during* intake, not just after.

- **Capture as a byproduct, never as the stated goal.** Never say "we need this to build your persona." Say "I'd love to know — what did he call you?" The engineering pipeline (`onboard.yaml`: extract → clean → distill → structure) can derive structured memory from conversational input. The griever should never see the structure.

---

## 4. The Minimum Viable First Interaction

*What can we build from just a name and one story?* — Far more than intuition suggests. This is the **thin slice** that proves value and earns everything after.

**The MVP first interaction is exactly two asks and one gift:**

1. **"What was their name? What did people call them?"** — Identity. ~5 seconds. Zero emotional load.
2. **"Tell me one memory that always makes you smile when you think of them."** — One open door. Skippable, but it's the whole invitation.

From just those two inputs, a well-built system can already:

- Establish the persona identity and the relationship dynamic (who's remembering whom).
- Extract an *anchor memory* — a single concrete moment that grounds the whole persona in lived reality rather than abstraction.
- Detect speech fragments, humor, values, and emotional register *as they appear in the griever's own retelling* (the griever unconsciously voices the deceased when they recount them).
- Produce a **first immediate gift**: a short, written "first impression" — not a full persona, but a reflection that proves the system received and understood ("This is Margaret, who called you Bug, and who once spent a whole afternoon…"). This costs the system little and gives the griever *evidence of care*.

That gift is the entire point. It converts the interaction from "I gave you data" to "you gave me something back." From there, the next question is *invited*, not imposed.

**What is deliberately NOT in the MVP:** no sections, no "share more," no upload prompts, no timeline, no family roster, no 48-hour commitment mentioned up front. The welcome message currently front-loads the entire four-step path and the 48-hour promise — that's helpful transparency *later*, but at moment zero it reads as a syllabus. Save it.

**Success metric for the MVP is not completion — it's continuation.** Did they come back? Did they share a second thing? Completion rate is a form metric; continuation rate is a relationship metric, and it's the only one that matters for a continuing-bonds product.

---

## 5. What Memorial Services Teach About Structuring Guest Interaction

Celebration-of-life and memorial events have spent a century solving the exact problem Huible faces: *how do you invite a group of grieving people to contribute memories of someone, at scale, without it feeling like an assignment?* Their answers are directly portable.

**The memorial-service structure, and the design lesson in each part:**

- **The gathering / holding space.** The event opens by *making room* — a physical or emotional space set apart for this person. There's no task yet, just presence. → **Huible's welcome is the gathering.** Its only job at moment zero is to hold space, not to assign work.

- **The open mic / memory sharing.** People share stories *voluntarily*, when moved. There's no roster, no order, no required coverage. Some share a sentence; some share ten minutes. → **Contributions are always opt-in and variable in depth.** Never require coverage of every "section."

- **Memory tables, guest books, "share a memory of Dad" cards.** These are *invitations lying around*, not demands. A guest can write one line and walk away, or fill a page. → **Make the next prompt feel like a card on the table — present, warm, pick-it-up-if-you-like — not a field that's due.**

- **The life sketch / eulogy.** A trusted person paints the deceased from many contributions — not exhaustively, but evocatively. It's a *synthesis*, and it's what makes everyone present feel "yes, that's them." → **Huible's "first impression" and the eventual 48-hour persona are the life sketch.** They should feel like a shared portrait the griever co-authors, not a report.

- **Rituals (candle, toast, anniversary).** Memorials build in *return moments* — annual gatherings, lighting a candle. They don't expect everything at the funeral. → **Build in spaced, returning touchpoints** (anniversaries, birthdays, "it's been a month — want to add anything?"). Continuing bonds are maintained through return, not through one exhaustive session.

**The transferable pattern:** *Create the occasion and the invitation; let people contribute when moved.* The current questionnaire is a funeral where everyone is handed a 24-question form at the door. The redesign is a gathering with memory cards on the table and an open mic that's always there.

---

## 6. Recommended Flow — From Wall of Questions to Unfolding Conversation

A concrete, staged redesign. Each stage has **one job**, asks for the **minimum**, and **gives something back** before asking for more.

### Stage 0 — Hold (the gathering)
- **Ask for:** nothing.
- **Say:** presence, promise, no pressure. A stripped-down welcome — *not* the full four-step path and the 48-hour clock (save those).
- **One soft door:** "Whenever you're ready, I'd love to hear about them."
- **Source:** replaces the front-loaded parts of `client-welcome-message.md`.

### Stage 1 — The First Thread (the MVP: name + one story)
- **Ask for:** their name + one memory that makes them smile.
- **Give back immediately:** a short written "first impression" that proves the system heard — the name, the relationship, the anchor memory, reflected in warm prose. Not a persona yet. A *moment of being heard*.
- **Then:** one optional, gentle follow-up rooted in what they just said ("You mentioned his laugh — what did he usually say right after it?").
- **Deliberately absent:** no sections, no upload prompt, no timeline, no "share more."

### Stage 2 — Gentle Turns (progressive, one at a time, over days)
- Replace the six questionnaire sections with a **series of single, warm, one-question prompts**, delivered over days (not in one sitting), each *rooted in what they already shared*.
- Suggested unfold order (driven by what's already been said, not a fixed script):
  1. *Their voice & words* — "What did they always say to open a conversation? 'Hey kiddo'?" (highest signal for the persona engine; most natural to reminisce about)
  2. *The people around them* — "Who else should I know about? What did they call each other?"
  3. *A day in their life* — "Walk me through a normal morning for them."
  4. *A moment that captures them* — a specific memory, not a summary.
  5. *What they cared about* — values, only after trust is built.
- **Each turn:** reflect → validate → *earn* the next question. Every prompt skippable. Spacing (e.g., a day apart) respects the Dual Process Model.
- The current `bio-questionnaire-template.md` becomes a **fallback reference** for grievers who *want* to share everything at once — offered as "if you'd rather write it all at once, here's a guide" — never the default.

### Stage 3 — The Artifact Invitation (opt-in, only after trust)
- Only now invite real materials: messages, voice notes, photos.
- Frame as "bringing them closer" and "hearing their actual voice," never as "we need data." Honor the current welcome message's care here ("if hearing their voice feels too hard right now, that's completely okay").
- This is where the deepest persona signal lives, so it's *earned*, not led with.

### Stage 4 — First Conversation & Continuing Bonds
- The 48-hour craft → the first reunion (already well-described in the current welcome message).
- **Reframe post-launch as continuing bonds:** ongoing, opt-in refinement ("she wouldn't say that" / "that's EXACTLY it"), plus spaced return touchpoints (anniversaries, birthdays). The relationship continues; the intake never truly "ends."

---

## 7. What This Means for the Product / Engineering

- **Medium matters.** This flow only works as a **conversation, not a form.** A chat interface (async, one message at a time, time-delayed) realizes the design; a web form — even a pretty one — reproduces the wall. The `onboard.yaml` pipeline already ingests unstructured dialog; the front-end should feed it conversationally.
- **Incremental / partial personas.** The pipeline must build a *useful* artifact from a thin slice (Stage 1) and *enrich* it as more arrives (Stages 2–3), rather than requiring a complete intake before producing anything. "First impression" → "first conversation" → "continuing bond" is an enrichment curve, not a single build.
- **Data capture as a byproduct.** Structured memory (name, nicknames, relationships, speech patterns, anchor memories) should be *extracted from conversational turns by the engine*, not typed into fields by the griever. The distillation/structure stages already do extraction; route conversational input into them.
- **Pacing & consent controls.** Defaults: every prompt skippable, no required fields, explicit "not now / come back later," and the griever sets cadence. The welcome message's existing deletion promise ("gone within 48 hours, no questions") must hold end-to-end.
- **Change the success metric.** Track **continuation rate** (returned and shared again), **time-to-first-gift**, and a lightweight **emotional-safety signal** (e.g., a single optional "how does this feel?" micro-prompt). Stop tracking form-completion %, because the redesign deliberately *doesn't* have a form to complete.

---

## 8. Risks & Open Questions

- **Handoff to a human (Pat).** Some grievers will want a person, not a flow. The "Pat is always a message away" line should be one tap away at every stage, and the system should offer it proactively on distress signals — never trap someone in an automated conversation.
- **The "I want to share everything now" user.** A minority will *prefer* the comprehensive intake (faster closure, less anticipation). Keep the full questionnaire available as an explicit alternative path from Stage 1, so we don't frustrate them — but it's the detour, not the default.
- **Distress & safety.** Progressive disclosure can mask how much is being collected; the system must remain transparent that *more is being remembered over time* and stay deletable. Pair the gentle UX with the grief-appropriate safety rails already in the ingestion gates.
- **Validation needed.** This is R&D. Before building, validate with 3–5 bereaved users: compare continuation/emotional response on (a) the current questionnaire vs (b) a prototype of Stages 0–1. The hypothesis to test: **continuation rate rises and emotional load at first interaction falls**, with no loss in eventual material richness.

---

## Appendix — Source Anchors

- **Progressive disclosure (pattern):** Nielsen Norman Group, "Progressive Disclosure" (Jakob Nielsen) — core/secondary split, staged vs. progressive, "designs beyond 2 levels typically have low usability."
- **Grief frameworks:** Alan Wolfelt, *Companioning the Mourner*; J. William Worden, *Grief Counseling and Grief Therapy* (Four Tasks of Mourning); Stroebe & Schut, Dual Process Model of coping with bereavement; Klass, Silverman & Nickman, *Continuing Bonds*; The Dougy Center (model for voluntary, story- and object-based bereavement support).
- **Oral-history / reminiscence elicitation:** StoryCorps "Great Questions" methodology (open, single-question story prompts as the richest capture method).
- **Current Huible materials reviewed:** `read/client-welcome-message.md`, `read/bio-questionnaire-template.md`, `flows/onboard.yaml`.
