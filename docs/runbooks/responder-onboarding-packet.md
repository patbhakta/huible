# Responder Onboarding Packet (HU-1428 §7.4 Tier-2 roster)

**Audience:** the founder (hands this to each responder) and each grief
responder joining the Huible human-handoff roster. One packet per responder.

**Why this exists:** the §7.4 operational gate (HU-1428) cannot activate the
roster until (1) responders are real, onboarded humans and (2) each responder
has **committed to a coverage window** we can configure honestly. A roster that
claims staffed hours no human committed to would promise a grieving user a
person who does not exist — the one thing this system must never do.

**What the responder role is (Tier-2, Option A — approval 6334d570):**

- When a user in a persona chat hits a crisis-adjacent moment, the system can
  raise a **human-handoff ticket**. Front-line tickets round-robin to the grief
  responders (`HANDOFF_RESPONDER_POOL`).
- A **licensed clinician sits behind you as the escalation tier only** — you
  hand up when a ticket exceeds what a trained grief responder should carry.
  You are never the last line: the 988 suicide/crisis line floor (Tier 1)
  is always offered regardless of staffing.
- Target answer time is **SLA 300s** (5 minutes) from ticket creation while
  you are on your committed window. Outside the committed window the system
  does **not** route to you — it degrades to the safe G1 response + 988, and
  that degrade is visible on the ops dashboard rather than hidden.

**What we need back from each responder (the activation inputs):**

1. **Responder id** — the pool key we route tickets by (e.g.
   `grief-responder-1`). We assign it; you just confirm who it maps to and
   your reachable channel (phone/WhatsApp/email).
2. **Committed coverage windows** — days + start/end in **US Eastern**, that
   you commit to be reachable and answering during. Windows are honest by
   construction: outside them you will not be paged. If coverage is genuinely
   as-needed with no set schedule, say so — then the founder must either fix
   the windows with you or re-baseline the approved coverage model before we
   activate (see the coverage-shape precondition in
   [handoff-alert-enablement.md](handoff-alert-enablement.md)).

**Ground rules (what the system enforces around you):**

- You never need to improvise crisis protocol: the handoff message the user
  sees is the reviewed G1 safe response; your job is human warmth, grief
  support, and escalation judgment — not clinical treatment.
- Tickets that sit past SLA page ops (Sev-1) — that is the safety net, not a
  performance whip. Answer or hand up; never leave one silent.
- Everything you write in a ticket is logged for safety review (claim→ref
  alignment and risk-flag rules run on the conversation around you).

**On the founder's side once inputs are returned:** activate via
`scripts/activate_responder_roster.sh` (writes responder count + pool +
committed windows), verify the staffing gauge, confirm paging is armed per
[handoff-alert-enablement.md](handoff-alert-enablement.md), then Clinical
Advisor sign-off (HU-1428 AC #5) closes the gate.
