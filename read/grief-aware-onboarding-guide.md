# Grief-Aware Onboarding Guide (Clinical Recommendations)

> **Audience:** Huible product, engineering, and client-facing teams (Pat Bhakta, onboarding flow owners, Tier-2/advisory implementers).
> **Author:** Clinical Advisor. Status: clinical guidance, not a medical protocol.
> **Scope:** How Huible should onboard clients who may be grieving, from first signup through first conversation.
> **Grounding materials reviewed:** `read/client-welcome-message.md`, `read/bio-questionnaire-template.md`, and the safety/clinical gaps noted in `docs/LETTUCEAI_ANALYSIS.md` (§3.2, §3.5) and `docs/COMPETITIVE_ROADMAP.md` (Phase 3).
> **Note:** The issue references `read/client-onboarding-experience.md`, which is not present in the repo. This guide is based on the two existing onboarding touchpoints listed above. If that file exists elsewhere, treat this guide as applicable to it as well.

---

## 0. Framing: What Huible Is — And Is Not — In A Grief Context

Before any flow design, the whole team must hold one shared clinical frame:

- **Huible is a remembrance product, not a therapy product.** It is not a substitute for grief counseling, psychiatry, or pastoral care. The README is correct: Huible is explicitly "NOT a therapy chatbot."
- **Continuing Bonds is real and can be healthy.** Modern grief research (Klass, Silverman & Nickman) rejects the old "you must detach and move on" model. Maintaining a *connection* with the deceased — through ritual, memory, conversation — is a normal, healthy part of integrating a loss. Huible sits in this tradition.
- **But Continuing Bonds can become maladaptive.** The bond becomes harmful when it freezes the mourner in *denial of the death*, when it *replaces* engagement with the living world, or when it is used to *avoid* the pain of loss rather than metabolize it. Huible's onboarding must actively steer toward the healthy form and away from the harmful one.
- **The single biggest clinical risk unique to Huible is the "disclosure/heartbreak boundary"** (called out in `docs/LETTUCEAI_ANALYSIS.md` §3.2). An AI that *sounds like* a dead loved one can (a) erode the mourner's acceptance of the death (Worden's Task 1), (b) create dependency, and (c) cause a *second* heartbreak when the persona inevitably says something "wrong." Every onboarding decision below is in service of containing that risk.

**Design principle for the entire flow:** the client must stay *in control of pace, depth, and continuation*, the product must stay *honest about what it is*, and there must be *a warm, non-punitive path to professional support* whenever the client's needs exceed what a remembrance product can safely hold.

---

## 1. Emotional Stages To Expect — And How The Process Should Adapt

### 1.1 Do not use the Kübler-Ross "five stages" as an operating model

The DABDA model (Denial → Anger → Bargaining → Depression → Acceptance) is the most famous grief model and also the most misused. It was derived from observing the *dying*, not the *bereaved*, and it is **not linear, not universal, and not prescriptive.** Treating onboarding as "advance the client through five stages" will misread most real people. Do not build flow logic against it.

### 1.2 Use three clinically useful lenses instead

**Lens A — The Dual Process Model (Stroebe & Schut).** Grieving people *oscillate* between:
- **Loss orientation** — feeling the grief, crying, yearning, looking at photos, remembering.
- **Restoration orientation** — doing practical life, distracting, taking care of tasks, engaging with the world.

A healthy mourner swings between both. Implication for Huible: the onboarding flow must tolerate a client who is fully in loss-orientation one day (wants to share everything, emotionally) and fully in restoration-orientation the next (wants to step away, attend to life). **Never penalize the swing.** "Save and return" is not a courtesy feature — it is a clinical accommodation.

**Lens B — Worden's Four Tasks of Mourning.** Mourning is *work*, and it has four tasks (not stages): (1) accept the reality of the loss, (2) process the pain, (3) adjust to a world without them, (4) find an enduring connection while reinvesting in life. Huible is most *safe and most valuable* when a client has made meaningful progress on Task 1 (basic acceptance of the death) and is working on Task 4 (continuing the bond in a healthy way). Huible is *most risky* when a client is stuck before Task 1 — i.e., they have not accepted the death — because a convincing voice clone can actively *interfere* with Task 1.

**Lens C — Bonanno's resilience trajectories. ~60% of bereaved people are resilient**, ~10–15% experience prolonged/complicated grief, and the remainder show depression or slow recovery. **Implication: do not pathologize normal grief.** Most clients will be hurting but okay. Clinical intervention is for the minority. Onboarding should be *gentle for the majority* and *triage-capable for the minority*, not clinicalized for everyone.

### 1.3 Acute grief has specific cognitive/functional effects the flow must accommodate

In the first weeks-to-months (acute grief), clients commonly experience:
- **"Grief brain"** — impaired concentration, memory, and decision-making. Keep every step short. Reduce choices. Never present a wall of required fields.
- **Emotional flooding** — sudden, overwhelming waves of feeling. Every screen must allow immediate exit and graceful resume.
- **Numbness / dissociation** — some clients feel *nothing* and feel guilty about that. The flow should never imply there is a "right" way to feel.
- **Time distortion** — the loss feels like yesterday *and* a lifetime ago simultaneously. Avoid time-pressure language ("only 48 hours left").

### 1.4 Concrete adaptations to the onboarding process

| Stage of grief the client may be in | Adaptation |
|---|---|
| Acute (first days–weeks), high shock | **Do not** push to data collection. Cooling-off period (see §6). Low-friction welcome only. |
| Emotional volatility, oscillation | All sections save-and-resume; nothing expires; no "abandoned cart" pressure language. |
| Cognitive overload | One question concept per screen. Generous defaults. "Skip" always available. Progress is preserved. |
| Healthy continuing-bond orientation (most clients) | The current questionnaire + welcome message serve them well. Preserve the warm tone. |
| Avoidant (can't yet look at materials) | Allow materials-upload to be deferred indefinitely. A persona can begin from the bio alone. |
| Stuck in denial / not accepting death | **Triage (§3).** This is the population for whom the voice clone is clinically riskiest. |

---

## 2. When Data Collection Becomes Re-Traumatization — And How To Prevent It

### 2.1 What re-traumatization actually is

Re-traumatization occurs when a person is *compelled to re-experience a traumatic event* in a way that **(a) removes their sense of control, (b) exceeds their current capacity to regulate emotion, or (c) reproduces the helplessness of the original event.** For Huible, the traumatic event is usually *the death itself* and/or the *circumstances* of the death (sudden, violent, witnessed, suicide, loss of a child). The risk is not in *remembering the person* — that is usually wanted and healing. The risk is in *being pressed to recall the loss* or in *losing agency over the recall process*.

### 2.2 The four re-traumatization vectors in onboarding

| Vector | How it shows up in onboarding | Prevention |
|---|---|---|
| **Loss of agency** | Required fields, locked sections, "you must complete X before Y," inability to skip | Every question optional. No gating. Save-and-return always. The client decides volume, depth, and pace. |
| **Pressured recall of the death** | Any question about *how/when/where they died*, last moments, final conversations, medical details, the funeral | **Do not ask.** The current bio questionnaire correctly focuses on *how they lived, not how they died* — keep it that way. Never collect cause/manner/circumstance of death as onboarding data. If ever needed for a safety model, collect it separately, optionally, with an explicit content warning. |
| **Time pressure** | Countdown timers, "complete within 48 hours," urgency language | Remove all of it. The 48-hour figure in the current materials refers to Huible's *build time after materials are submitted* — that is fine. Never attach urgency to the *client's* sharing. |
| **Relational judgment** | Questions that imply the relationship was insufficient, or that "correct" answers exist | The current questionnaire handles this well ("I don't know is a complete answer," "nothing is too small"). Preserve and extend this posture. |

### 2.3 Concrete prevention rules for the onboarding build

1. **Default to opt-in, never opt-out.** Sensitive sections (e.g., "a bittersweet or difficult memory — only if you feel comfortable") must be explicitly chosen, not pre-checked.
2. **Content warnings before any emotionally heavy section**, however gentle. Example: *"The next part is about memories. You can skip any of it."*
3. **No "last seen" guilt mechanics.** No "you started this 3 days ago, finish now."
4. **Materials upload is voluntary and deferrable.** A client who cannot yet look at the deceased's text messages should be able to proceed with the bio questionnaire alone, and add materials later — or never.
5. **Human (Pat) availability is always one message away** — already in the welcome message; preserve it as a first-class contact path, not a footnote.
6. **Never auto-prompt with the deceased's own words during onboarding.** Surfacing a real old message from the deceased as an onboarding nudge (e.g., to "get them to share more") is a known re-traumatization vector. Onboarding prompts come from Huible/Pat, never impersonating or quoting the deceased.
7. **Deletion must be instant and total on request.** The current copy says "within 48 hours." For a grieving person who is panicking about what they shared, 48 hours can feel like an eternity. Offer *immediate* soft-delete (revokes access, hides everywhere) with hard-delete to follow, and tell the client it's gone the moment they ask.

---

## 3. Safety Rails — When To Recommend Professional Grief Support

Huible is not equipped to hold acute psychiatric risk. The advisory/safety layer (currently empty — see `docs/LETTUCEAI_ANALYSIS.md` §4) must be able to **detect** the signals below during onboarding and **warmly route** the client to professional support. This is a *triage* function, not a diagnostic one.

### 3.1 Red-flag indicators (any one warrants a warm referral)

Group these into the Tier-2/advisory model as **elevated-risk signals**, not hard blocks.

**Acute crisis (act immediately — these are not onboarding problems, they are emergencies):**
- Suicidal ideation, intent, or plan ("I don't want to live without them," "I want to join them").
- Inability to care for self / dependents (not eating, not sleeping for days, psychotic features).
- Intoxication as the coping mechanism described in onboarding text.
- *Protocol:* Do not proceed with onboarding. Provide crisis lines (988 in the US; international equivalents) prominently. A human (Pat) reaches out directly. Log the event.

**High complicated-grief risk (warm referral + soft hold):**
- The loss was **recent** (within ~days to a few weeks) *and* the client is in visible shock. (See cooling-off, §6.)
- The death was **traumatic** (suicide, homicide, accident, witnessed, death of a child, perinatal loss). These carry 2–10× higher complicated-grief rates.
- Client language indicating **non-acceptance of the death** as the organizing reality ("they're not really gone," "this will bring them back," "I'm just waiting for them to come back").
- **Intense, unrelenting yearning** that the client describes as unbearable and unchanging.
- **Pervasive meaninglessness** ("nothing matters without them," "my life is over").
- **Severe guilt or self-blame** about the death, especially survivor's guilt.
- **Complete avoidance** of all reminders *or* the opposite — the client cannot stop engaging with the deceased to the exclusion of all else.
- Client is a **minor**, or the deceased is the client's **child** (highest-risk bereavement category).

### 3.2 How the referral should feel (this matters as much as the trigger)

A referral that reads as rejection ("you're too messed up for us") will harm and will be ignored. The referral must read as *care*:

- **Non-stigmatizing.** Never "you need help." Prefer *"We want to make sure you're supported in every way you deserve,"* and present professional support as *in addition to*, not *instead of*, Huible.
- **A warm handoff, not a cold link.** Ideally a human (Pat) makes contact. At minimum, resources are presented personally, not as a generic footer.
- **The door stays open.** A client who is in acute grief *now* may be a healthy continuing-bonds client *in three months*. Preserve their account, their materials, their place. Re-engagement is welcome later.
- **Not punitive and not permanent.** A "soft hold" means onboarding pauses with the client's consent and a check-in; it is not a ban.

### 3.3 Maintain a curated, current referral list

Maintain a region-aware list of: licensed grief counselors, complicated-grief (CG/PGD) specialists, suicide-loss support groups (e.g., AFSP), parents-who-have-lost-a-child groups (e.g., Compassionate Friends), and crisis lines. This is a clinical deliverable in itself and should be owned/refreshed quarterly. The advisory layer should surface the *right* resource for the signal, not a single generic link.

---

## 4. Handling A Client Who Is Clearly Not Ready

A client may be not-ready because they are in **acute shock**, because they are **stuck in non-acceptance**, or because they are being **pressured by family** to do this before they want to. All three require the same response shape.

### 4.1 Signs a client is not ready

- Communications are dissociated, incoherent, or shift between numb and overwhelmed within a single exchange.
- Cannot articulate basic, grounding facts about the deceased (name, relationship) without breaking down — *and* the loss is very recent.
- Explicitly states they "can't do this" or "aren't ready," yet continues to be pushed forward by the flow or by a third party.
- Expresses that the product will *return* the deceased, or reunite them in a literal sense (reality-testing is impaired).
- The request is coming from someone *other* than the primary bereaved (e.g., a relative signing someone up).

### 4.2 The response: gentle pause, not a wall

1. **Stop the data-collection march.** Do not advance to materials upload or bio questions.
2. **Acknowledge, without judgment.** *"There is absolutely no rush. What you're feeling makes complete sense."*
3. **Offer the gentle pause.** Move the account into a paused-but-preserved state. Nothing expires. The welcome message stays available.
4. **Warm human contact.** A human (Pat) reaches out — not to sell, not to resume, but to be present and to assess whether professional support is the better next step (§3).
5. **Remove all re-engagement pressure** for a defined cooling window (see §6), then resume only the softest of touches ("we're still here, whenever").
6. **If a third party is driving the signup**, gently confirm the primary bereaved person's own wishes before proceeding. Huible onboards the person who is grieving, not their proxy.

---

## 5. Harmful vs. Healing Language

Language is clinical in this domain. Words can wound as deeply as the loss itself. The current `client-welcome-message.md` and `bio-questionnaire-template.md` are, on the whole, **good** — they already embody many of the healing patterns below. The following codifies what to keep, what to extend, and what to never introduce.

### 5.1 Harmful language (never use)

| Phrase | Why it harms |
|---|---|
| "Move on" / "get over it" / "let go" | Implies the bond itself is the problem. Contradicts continuing-bonds. Induces shame. |
| "Closure" | Suggests grief has an endpoint and that the client should reach it. There is no closure for the loss of someone central to your life; there is only integration. |
| "They're in a better place" / "everything happens for a reason" | Religious/philosophical imposition; can enrage or alienate; not Huible's to say. |
| "At least..." (e.g., "at least they lived a long life") | Minimizes pain. "At least" always precedes a denial of the loss's weight. |
| "Time heals" / "you'll feel better soon" | False promise with a timeline. |
| "Be strong" / "they wouldn't want you to cry" | Denies the client the emotional processing that is itself the work of mourning. |
| "Bring them back" / "they're here again" / "talk to the real them" | **The single most dangerous category for Huible.** Implying the AI *is* the deceased erodes acceptance of the death (Worden Task 1) and sets up a devastating heartbreak. |
| "Normal grief" / "abnormal grief" | Pathologizes the mourner. Grief is idiosyncratic. |
| "You should be over it by now" (by date) | Grief has no schedule. |
| "Required" / "must" / "deadline" on client-facing sharing | Reintroduces the agency loss that causes re-traumatization (§2). |

### 5.2 Healing language (use liberally)

- *"There's no rush. No pressure. This is your journey, at your pace."* (Already in the welcome message — keep.)
- *"However you're feeling right now is okay."*
- *"Your relationship with them continues — in a different form."*
- *"Grief has no timeline."*
- *"This is a remembrance — a reflection of them, built from what you share. It won't be perfect, but it can feel close."* (Already in the welcome message — keep, and reinforce in every voice-touching surface.)
- *"You can stop at any time. You can come back any time. You can change your mind."*
- *"Everything you share is yours."* (Agency + ownership.)
- *"I don't know is a complete answer."* (Already in the questionnaire — keep.)
- When a section is heavy: *"Only if you'd like to."* / *"Some memories hurt in a beautiful way."* (Already present — excellent.)

### 5.3 The one framing rule that must appear everywhere

The disclosure boundary — *this is a reflection, not the person* — must appear:
- in the welcome message (✓ already there),
- immediately before the first conversation with the persona,
- and in plain language the client can repeat back.

It is the ethical and clinical spine of the product. It protects the client's acceptance of the death, protects against dependency, and pre-softens the inevitable heartbreak when the persona is imperfect.

---

## 6. Should There Be A Cooling-Off Period Between Signup And Data Collection?

**Yes — a soft, opt-outable cooling-off period is clinically indicated.** A hard mandatory wait is not.

### 6.1 Why a cooling-off period helps

- **Acute grief impairs consent.** In the first days after a loss, people are often in shock, sleep-deprived, and cognitively impaired ("grief brain"). They are not in the best position to decide what to share, with whom, or to understand what an AI voice clone will feel like. A cooling-off window protects the integrity of their consent.
- **It reduces impulse regret.** People who act in the first wave of grief sometimes share things they later wish they hadn't, or commit to an experience they aren't ready for.
- **It protects the product.** Clients who were rushed are the ones who churn, complain, and have the worst first-conversation experience.

### 6.2 Recommended design — soft, not hard

A *hard wall* ("you cannot proceed for 14 days") is disrespectful to the mourner who is genuinely ready, and for some people *action is itself a healthy channel for grief* (restoration orientation, §1.2). Recommended instead:

1. **At signup:** deliver the welcome message and the bio questionnaire. Make clear there is no rush.
2. **Do not auto-nudge toward data collection for a minimum window** — recommended **7 days** for a general loss, **longer** (14–30 days) if any high-risk signal (§3.1) is detected. During this window the client may proceed if they actively choose to, but the product does not push.
3. **Detect recency.** If onboarding captures the date of loss (optional, content-warned, never required) and it is within ~2 weeks, default to the longer cooling window and surface professional resources alongside the welcome.
4. **After the window:** a single, gentle, non-pressuring nudge. *"We're still here whenever you're ready — no rush at all."* Then stop nudging. Let the client lead.
5. **Anyone can fast-track** by explicitly saying they're ready. The cooling-off is a default, not a gate.

### 6.3 What the cooling-off is NOT

- It is **not** a diagnostic hold. We are not deciding the client is "too grieving." We are giving the default space that most people in acute grief benefit from.
- It is **not** a substitute for §3 triage. If red flags are present, refer regardless of timing.

---

## 7. Recommended Onboarding Stage Sequence (Synthesis)

Bringing §1–§6 together into a concrete flow the product team can implement:

| # | Stage | Clinical purpose | Key property |
|---|---|---|---|
| 0 | **Welcome only.** Warm, honest, no data collection. | Set the frame (remembrance, not replacement); establish control + pace. | The client does nothing but read. |
| 1 | **Optional, content-warned loss context.** "Would you like to tell us roughly when this was? You don't have to." | Detect recency for cooling-off defaults + triage. | Fully skippable. Feeds §3/§6 logic only. |
| 2 | **Cooling-off default.** No push to collect for 7–30 days depending on signals. | Protect consent in acute grief. | Client may override and proceed. |
| 3 | **Bio questionnaire (current).** Life-focused, never death-focused. | Begin the continuing bond, on the client's terms. | All optional, save-and-resume. |
| 4 | **Materials upload (optional, deferred).** Texts, voice notes, photos. | Deeper texture; client chooses when/if. | Never gated behind bio; never required. |
| 5 | **Pre-first-conversation framing.** Restate the disclosure boundary clearly. | Protect acceptance of the death; pre-soften imperfection heartbreak. | Required screen, plain language. |
| 6 | **First conversation.** Then iterative feedback ("that's EXACTLY what she'd say"). | Healthy continuing bond. | Client-initiated; no pressure. |
| – | **At every stage:** one-tap access to Pat (human) and to professional resources. | Safety net. | Always visible, never stigmatizing. |

---

## 8. Open Clinical Items For The Engineering / Tier-2 Team

These map to gaps already flagged internally and are where clinical input most needs to become product behavior:

1. **Distress-detection signals in the advisory layer** (`docs/LETTUCEAI_ANALYSIS.md` §4 lists the advisory module as empty). The red-flag taxonomy in §3.1 above is the input spec for that detection.
2. **Emotion-as-safety-input** (§3.5 of the analysis). The user's emotional state is the primary safety signal, not persona texture. Onboarding is the first place this signal can be passively collected (tone, cadence, content) — with explicit privacy framing.
3. **Disclosure boundary enforcement in the generated persona.** The persona itself must never claim to *be* the deceased or to *be alive*. This is a generation-time rule, but onboarding sets the expectation that makes it coherent.
4. **Per-tenant safety policy** (§3.8 of the analysis). High-risk contexts (child loss, traumatic loss) may warrant a higher-touch default onboarding posture.

---

## 9. Summary Of Direct Answers To The Six Questions

1. **Emotional stages & adaptation** — Do not use the linear "five stages." Use the Dual Process Model (tolerate oscillation, save-and-resume), Worden's Tasks (Huible is safest after Task-1 acceptance, riskiest before it), and Bonanno's trajectories (most clients are resilient; triage the minority). Accommodate acute-grief cognitive overload with short steps, no required fields, no deadlines. (§1)
2. **Re-traumatization** — It is driven by *loss of agency, pressured recall of the death, time pressure, and relational judgment* — not by remembering the person. Prevent it with: everything optional, never ask about the death, no urgency language, voluntary/deferrable materials upload, no impersonation of the deceased during onboarding, instant soft-delete. (§2)
3. **Safety rails / referral** — Red flags: acute crisis (suicidality, self-neglect), recent traumatic loss, non-acceptance of the death, unbearable unrelenting yearning, meaninglessness, severe guilt, total avoidance or total fixation, loss of a child, minors. Respond with a *warm, non-stigmatizing, door-stays-open* referral, curated to the signal, ideally human-delivered. (§3)
4. **Client not ready** — Pause data collection, acknowledge without judgment, preserve everything, have a human reach out, confirm the primary bereaved's own wishes (not a proxy's), remove re-engagement pressure. Gentle pause, not a wall. (§4)
5. **Language** — Harmful: "move on," "closure," "let go," "better place," "at least," "be strong," "bring them back / talk to the real them," any timeline. Healing: "no rush," "your relationship continues," "grief has no timeline," "this is a reflection, not the person," "however you feel is okay," "I don't know is a complete answer." The disclosure boundary must appear on every voice-touching surface. (§5)
6. **Cooling-off period** — Yes, a *soft* one. Default 7 days (general) to 14–30 days (high-risk/recent), with no auto-nudge during the window and a single gentle touch after. The client can always fast-track by explicitly choosing to. It protects consent without disrespecting those who are ready and want to act. (§6)

---

*This guide is clinical recommendations for a remembrance product, not a medical protocol. It should be reviewed by a licensed grief clinician before the voice/Tier-1 launch and refreshed as the advisory layer (§8) comes online.*
