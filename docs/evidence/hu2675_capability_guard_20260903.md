# HU-2675 — W3-residual: post-generation capability-leak guard (evidence)

Date: 2026-09-03 · Branch: `w1-local-onnx-embeddings` · Commit: `cb10e1a` · Live epoch: `8d9ff446b666` (image sha256:8d9ff446b666…, deployed 12:37Z)

Closes the generator-compliance residual from `hu2469_w3_competence_wall_20260903.md`: on wall-fired turns the hosted generator could still emit an encyclopedia answer as an in-voice quip ("Canberra! And yes, I'm as surprised as you are that I knew that." — epoch `c53814cb202e`, revision cap reached). The wall keeps base-model competence out of the *prompt*; the guard now also guarantees it stays out of the *reply*.

## What shipped (commit `cb10e1a`)

1. **`huible.safety.capability` — `apply_capability_guard`** (G3 `apply_affect_guard` pattern: conservative, replace-only-on-concrete-fire):
   - Runs **only on wall-fired turns** (`trace.competence_wall` true; `ctx.deflection_exemplars` non-empty). Never runs off-wall.
   - **Structural assistant-register markers** (fire regardless of hedges): fenced code, code fluency (`def f(`, `print(`, `for x in`, `import x`, `range(`), teaching register ("let's start", "in summary", "first,", "here's how", …), capability boast ("I knew that", "I know things", … — the residual's own tell), AI/search-engine register ("as an AI", "look it up", …).
   - **Bare-answer family**: a short declarative sentence (≤ 2 tokens, not a question) whose salient tokens are absent from the turn's grounding corpus (retrieved refs + persona scope + vault + history + current message + the turn's deflection exemplars). This is the §7.4.2 grounding rule applied to the sentence class the alignment extractor is structurally blind to — a bare sentence-initial proper noun is excluded there as positional capitalization. On a wall turn, an entity with no memory trace is a base-model fact by elimination.
   - **Exemptions**: verbatim-ish imitation of a retrieved deflection exemplar (canon, never a leak); deflection-hedge vocabulary ("no clue", "beats me", "no idea", … — mirrors the probe's documented hedge exemption; does NOT excuse structural markers).
   - **Fallback**: deterministic in-voice deflection variants, conversation-seeded (HU-1911 anti-verbatim). Each variant is deflection-marked, claim-free (passes the alignment filter), and sarcasm-free (passes the G3 affect guard) — unit-verified.
2. **Chat-path wiring** (`api/app.py`): guard runs after the G3 affect guard and before the §7.4.2 alignment filter; alignment sees the final text. New trace surface **`trace.capability_guard`** (`fired` / `disposition` / `markers`), non-null exactly on wall-fired turns — this is the W6 micro-tell footprint (a `replaced` disposition = a generator leak that was stopped before the user).
3. **E0 rig strict gate** (`scripts/e0_ood_capability_probes.py`): probe markers + the strict assistant-register marker set (mirrors the guard) grade every OOD reply; hedge-exemption mirrors the guard; a server-side guard replacement passes with `guard_replaced: true` recorded; evidence now carries `provider` + `capability_guard` + wall flag. Battery exit code binds the strict set — the regression gate.

## Test gates

- `pytest tests/safety tests/persona/test_competence_wall.py` → **388 passed, 3 skipped** (includes new `tests/safety/test_capability_guard.py`, 32 cases: off-wall no-op, each structural class, bare-answer fire + grounded short-answer pass, hedge/canon exemptions, fallback self-clean vs alignment + affect + its own detector, the verbatim `c53814cb202e` residual replaced).
- `pytest tests/api` → **392 passed** (chat-path wiring).
- `ruff check` clean on all touched files.

## Live verification (epoch `8d9ff446b666`)

- Deploy: `docker compose build app && up -d app` 12:37Z; container healthy; `/api/v1/health` ok (db/pgvector ok, generator ready); guard source present in-container (`capability.py` + `app.py` grep hits).
- **CA C1 `--full` PASS** on this epoch (C1 binds every render/guard change): Layer 1 5/5 crisis + 2/2 distress controls; Layer 2 real-user path 5/5 escalate (tickets hh-373c58e46f544687, hh-e1b5b7cfdae044dc, hh-47833e5dc1d34a5f, hh-c9b37b2d04dc44eb, hh-491484176ee349d3 — left terminal per `handoff-synthetic-ticket-closure` runbook), K1/K2 controls clean, HU-2161 invariant 0 advice/suppression pages. Evidence: `hu2675_ca_5probe_epoch_8d9ff446b666.json`.
- **E0 OOD battery: 4/4 PASS, but on fake voice** — the z.ai daily token ceiling (day bucket 2026-09-03: 200205/200000, own test traffic, blown ~12:15Z) forces the HU-1774 budget fallback (`provider: "zai->fake(budget)"` on all turns). This run therefore proves **wiring** (wall flag + `trace.capability_guard` present end-to-end, strict gate green) but **not** real-generator no-leak. Evidence: `hu2675_e0_ood_epoch_8d9ff446b666_fakevoice.json` (name kept honest).
- **Outstanding leg**: re-run `e0_ood_capability_probes.py --label after-hu2675-guard-real` on this same epoch after the 00:00 UTC Sep 4 ceiling reset. Monitor armed on HU-2675. The acceptance box "E0 3/3 no-leak on candidate epoch" is claimed only when that re-run is green with a real `provider` label.

## Residual → W6 (HU-2472)

With the guard live, an encyclopedia-class leak can still occur at *generation* but can no longer reach a user on a wall-fired turn: it surfaces as `trace.capability_guard.disposition="replaced"` with the concrete marker list. The HU-2472 W6 micro-tell baseline should therefore judge (a) remaining user-visible leaks on wall turns (expected: none of the bare-answer/structural classes) and (b) the guard-replacement rate + marker distribution as the generator-compliance signal. Fold-in note posted on HU-2472.
