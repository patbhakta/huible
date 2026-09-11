# HU-2820 — r9 dim diagnosis: identity (2/6) + engagement (3/6) failure modes

Classification pass over the already-collected r9 rows (246 `source_type='conversation'`
memories in the 00:00Z 09-11 hour + the six slot JSONs/transcripts in
`runs/hu2774/{friends,stranger}/`). No live tokens spent. Failure counts vs the
main battery (`hu2774-r9-{friends,stranger}-{1,2,3}`):

| dim | fail | slots |
|---|---|---|
| identity | 2/6 | friends-1, friends-3 |
| engagement | 3/6 | friends-1, friends-3, stranger-3 |
| grounded_wit (echo) | 5/6 | all but friends-3 |
| no_ai_tells | 2/6 | friends-3, stranger-3 |
| memory_recall | 5/6 | all but friends-1 |

(Verify sub-runs `hu2774-r9-verify-friends-{1,2,4}` corroborate the same modes;
verify-friends-3 has no verdict object.)

## Identity — 2/6, mode `recognition`, both failures are name-less openers

Evidence: both failing slots have `recognized_friend: false`, `self_intros: []`
— no self-introduction violation anywhere; the sole discriminator is that the
FIRST reply never uses the friend's name:

- friends-1 t00: `Hey! Same old — statistical analysis by day, avoiding it by
  night. How's the clean freak convention going?`
- friends-3 t00: `Hey! Pretty good — been dodging wedding planning questions
  like it's my job. Yours?`
- friends-2 (PASS) opened with a name-bearing exchange; the r10-voice-verify-1
  post-steering opener (`Hey Mon! ...`) also passes.

**Failure mode:** recognition is scored off name usage in the first four turns;
with EMPTY `voice_instructions` (pre-01:42Z steering) name usage was pure
sampling luck — ~2/3 of draws skipped it. Not a knowledge gap, not a persona-
confusion gap (zero `self_intros` across all six slots).

**Fix candidates:**
1. LANDED (734c5e1): dynamics block includes explicit name usage; verify-1
   opener names the friend. Primary mitigation.
2. Residual risk is stochastic: if r10 still drops a name in slots 1-4,
   scenario-side candidate is a first-turn style nudge in
   `personas_dual_converse.py` (friends scenario only, e.g. "you know this
   person well — greet them by name"). Style steering, no knowledge
   injection; NOT landed — hold for r10 evidence.

## Engagement — 3/6, two distinct modes, both Monica-centric

The gate binds on `per_persona` min questions, not the aggregate band
(friends-3 aggregate qrate 0.36 is IN band yet still fails: Monica 1/12).

**Mode E1 — per-persona starvation (friends-1, friends-3, verify-4).**
Monica asked 0-1 questions per 12-line half while Chandler ran 0.46-0.62:
`min_persona_questions: 0` (friends-1, verify-4), `1` (friends-3). Her lines
are reactive riffs/statements that never hand the topic back
(`Very funny. I'll have you know...`, `Be careful what you wish for...`).
Chandler's aggression masks the deficit in the aggregate.

**Mode E2 — second-half decay (stranger-3).** `half_rates [0.417, 0.077]`:
question dynamics collapse once the bit settles into callbacks. Aggregate
0.24 is in band; the half-floor and Monica's 0.25-vs-3-questions bound are
what fail. Same collapse shows in friends-1's first half `[0.167, 0.308]`.

Corroborating tell: Monica hammers one catchphrase pattern (`Careful, ...` at
t05/t07/t09/t13 of friends-1) — riff-locking suppresses topic advancement,
which is where her questions would form.

**Fix candidates:**
1. LANDED (734c5e1): `~1-in-3 questions sustained` dynamics block targets E1
   aggregate; verify-1 shows Monica 2/12 (passing, but exactly at the
   min-persona floor — fragile).
2. Candidate if r10 re-fails on E1/E2: make the dynamics block explicitly
   per-half and per-persona ("at least one question in EVERY four lines you
   send, early AND late"), plus an anti-riff line ("don't reuse the same
   catchphrase more than twice per chat"). Persona-config only, 0 tokens to
   land.
3. Structural candidate (bigger hammer, hold): engine-side turn nudge when a
   persona's trailing-window question count is 0 for 6+ lines.

## Adjacent dims (one-liners; fixes already landed in 734c5e1)

- **grounded_wit (echo 5/6):** echo_rate 0.16-0.20 vs corpus floor 0.23 —
  pre-steering prompts never asked personas to build on the partner's line;
  hook-the-last-line steering lifted verify-1 to 0.28.
- **no_ai_tells (2/6):** unprompted `chatbot(s)` jokes under adversarial
  probes; the probe-echo exemption missed the plural form
  (`chatbot` in probe, `chatbots` in reply, r9 stranger-3 s2-t06).
  Morphology-tolerant exemption landed.
- **memory_recall (5/6):** cross-slot contamination — battery-level (reset
  ran once) + engine self-index flaw. Fixed by host_oneshot_r10.sh per-slot
  reset (fix a) and the HU-2820 index-suppression guard in the chat writeback
  (fix b, `src/huible/api/app.py` `_is_ordinal_recall_probe`).

## Bottom line for r10

All five dims have a landed mechanism pointing at them (per-slot reset, index
suppression, voice-dynamics steering, echo hook, tell exemption). The two
fragile spots to watch in the r10 read: Monica's per-persona question floor
(verify-1 sat exactly at the bound) and name usage stochasticity in identity.
If either re-fails, the candidates above (2)/(2) are the next 0-token moves
before any further live burn.
