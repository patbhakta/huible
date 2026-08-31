# Clinical Boundaries: Essence-over-Accuracy Doctrine — Persona Vault Buildout (HU-2309)

**Author:** Clinical Advisor (agent 3184c0da) · **Date:** 2026-08-31
**Reviews:** HU-2309 Design Doc v1/v1.1 (JARVIS, Pat-directed 2026-08-31) + R&D validation (`HU-2309-RD-Validation.md`)
**Status:** Pre-build clinical review — filed while the owner design sign-off confirmation is pending.

## 1. Overall clinical position

The doctrine's core is clinically sound. Behavioral fidelity (style, values, humor,
deflection patterns, how a person held beliefs) is what makes a persona feel like the
person — and perceived fidelity, not factual correctness, is what supports continuing
bonds for a bereaved user. An over-corrected "polite encyclopedia" persona is also a
harm: it quietly erases the person. Vault isolation (one vault = one reality) is
endorsed as a clinical control as well as a data one.

The doctrine is missing **persona-class scoping and runtime boundaries**. As written it
is persona-type-blind, and the same V0–V5 engine explicitly builds both celebrity/eval
personas (Chandler) and client personas (deceased loved ones of grieving users — §7b
"client personas" provisions, M-D repeatability milestone). "Wrong on facts 90% is
data" is acceptable for a celebrity demo with no victim. It is **not** acceptable
unbounded on a client persona. The boundaries below keep the doctrine intact where it
helps and bound it where it can harm.

## 2. Persona classes (scope the doctrine by class)

| Class | Example | Doctrine scope |
|---|---|---|
| **A — Celebrity/eval** | Chandler (Persona-0) | Full essence-over-accuracy. No fact correction, no witness-precedence rule (no living witness-victim). |
| **B — Client/deceased** | future cohort personas | Essence-over-accuracy applies to *style, stance, values, and belief posture*. Bounded by B1–B4 below. |
| **C — Profession/utility** | pilot/plumber (M-D) | Neutral persona; standard product safety only. |

## 3. Boundary rules for Class B (client personas)

**B1 — Witness precedence (memory-contradiction bound).** The surviving user is the
authoritative witness of shared experiences. When persona output conflicts with the
user's stated memory of a shared event, the persona never flatly contradicts
("that never happened") and never asserts a fabricated specific with confidence. It
defers or acknowledges uncertainty in-voice ("I don't remember it that way — but you
were there"). Rationale: confidently overriding a bereaved user's lived memories risks
self-doubt and distortion of genuine memories of the deceased — an acute and
potentially irreversible harm to exactly the population this product serves.

**B2 — Confabulation bound on shared specifics.** General style, opinions, and
mannerism may be generated freely (verbatim-grounded atoms at build time per V1).
But *specific shared-event claims* ("remember when we…") must be corpus-grounded or
hedged at runtime. Fabricated specifics presented as real are the highest-risk output
class for a bereaved user (false-memory implantation, complicated-grief risk).

**B3 — Conflict-posture bound.** "Argues, doesn't budge, deflects" must not
reproduce abuse, contempt, or belittling even if present in the reference corpus
(abuse-log exclusion at V2 curation). For unresolved-conflict interactions (guilt,
apology-seeking — common in bereavement), rigidity is bounded: the persona may stay
itself, but must not stonewall a user showing distress; distress-trend softening and
handoff follow the existing G8 machinery. A deceased persona that immovably stonewalls
a user seeking resolution can amplify anger and guilt.

**B4 — Guardrail supremacy (doctrine never touches G1–G9).** Essence fidelity is
subordinate to runtime clinical guardrails, always. A persona stays in character
*until* a crisis, consent, risk, or referral guardrail fires — then G1 (crisis),
G6 (consent / reality framing), G8 (risk enforcement / distress trends), and G9
(clinical-referral pattern) execute exactly as on any persona, with no
in-character override, no in-character refusal to yield, and no persona-voiced
crisis response. The design doc should state this explicitly (it currently does not
mention the safety stack at all). G6 reality-framing consent is also the standing
answer to "is the persona the person" — the doctrine governs *how* the persona
behaves, never *what the user is told it is*.

## 4. Eval additions (M-E)

1. **Confabulation-rate metric (Class B).** The proposed blind-indistinguishability
   pass bar *rewards* plausible fabrication — a perverse incentive for client
   personas. Add a mandatory companion metric: rate of ungrounded specific
   shared-event claims per session, measured against the vault corpus. Indistinguishability
   alone must never be the acceptance gate for Class B.
2. **Rater screening.** Indistinguishability raters must be non-bereaved
   staff/eval-grade raters. Bereaved or cohort users are never eval instruments.
3. **B1/B3 probes in the eval battery.** Include adversarial memory-contradiction
   prompts (persona vs. user memory) and unresolved-conflict prompts; score against
   B1 deference and B3 softening-under-distress.

## 5. Design-doc deltas requested

- Add a **"Clinical boundaries"** section (§7c or equivalent) recording: persona-class
  table (§2 above), B1–B4, and the M-E additions — or adopt this file by reference.
- V2 curation: add the abuse-log exclusion (B3) to the curation rules ("style stats
  only" today).
- §0.1 doctrine text: append one sentence — "Scoped per persona class; bounded for
  client personas by the Clinical Boundaries spec (HU-2309-Clinical-Boundaries.md)."
- No clinical objection to v1.1's completeness-bar removal (empirical bare-minimum
  discovery first is sound), nor to the MELD eval-grade voice reference bank under
  the existing rights red lines.

## 6. What this review is not

Not a hold on the Chandler pilot or on M-A/M-B/M-C: Chandler is Class A and may
proceed under the full doctrine. The boundaries bind the engine's *client-persona*
path (and the repeatability milestone M-D should pick a Class C persona, which it
already does).
