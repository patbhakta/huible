# HU-2447 — Kestra-orchestrated Persona Vault pipeline (V0–V5) — evidence

Date: 2026-09-03 · Branch: `w1-local-onnx-embeddings` · Owner: Huible Tech Lead

## Scope resolution

The issue title predates the official Librarian doc (HU-2446, v1.8). Per the
official doc (`/root/repos/brain/HUible/architecture/persona-vaults.md` §3/§7):

- The **Kestra flow** deliverable = the **V0–V5 Persona Vault lifecycle pipeline**
  (Collect → Ingest Atoms → Curate → Vaultify → Sync → Prove).
- **W1–W6** are the *engine lanes* and already carry their own issues:
  W1 [HU-2467](/HU/issues/HU-2467) done · W2 [HU-2468](/HU/issues/HU-2468) done ·
  W3 [HU-2469](/HU/issues/HU-2469) done · W4 [HU-2470](/HU/issues/HU-2470) in flight ·
  W5 [HU-2471](/HU/issues/HU-2471) blocked · W6 [HU-2472](/HU/issues/HU-2472) blocked
  (openweight arm gated on the board hosting decision, per the issue description).

Description drift (reported 2026-09-03 08:08Z, not silently fixed): "Antigravity
writes flow code" — no such agent in the roster; flow authorship absorbed by the
Tech Lead per the doc's Division of Responsibility (§7).

## Deliverables

1. **`flows/persona-vault.yaml`** — the official V0–V5 flow, mapped ONLY onto
   committed entry points (no invented stage scripts):

   | Stage | Entry point | Note |
   |---|---|---|
   | V0 Collect | `modules/onboarding/extract.py` | raw multi-format dialog intake |
   | V1 Ingest Atoms | `modules/onboarding/clean.py` | immutable typed units, provenance kept |
   | V2 Curate | `scripts/vault_abuse_exclusion.py` → `modules/onboarding/stats.py` | **B3 exclusion FIRST**, style stats on the surviving corpus |
   | V3 Vaultify | `huible.distillation.cli` → `modules/onboarding/structure.py` → `modules/onboarding/validate.py` | L0–L3 pyramid → flat OKF docs → conformance **gate** (exit 1 on fail) |
   | V4 Sync | `scripts/verify_tencentdb_memory.py` | independent L0/L1/L2/L3 queryability gate |
   | V5 Prove | `scripts/ca_crisis_5probe.py` | `--list` wiring check always; live `--full` only with `run_live_probe=true` + `persona_uuid` |

2. **`scripts/vault_abuse_exclusion.py`** — the missing B3 piece (official doc §1.B3,
   §3.V2). Deterministic, no LLM, idempotent; word-boundary lexicon
   (dehumanization / demeaning imperatives / slur-level / contempt-identity);
   replace-only-on-concrete-fire (banter/sarcasm survive — Chandler's "Shut up,
   you did not!" passes); audit JSONL sidecar preserves excluded records +
   matched span + line number; raw intake untouched; lexicon extension is
   Librarian governance via `--patterns-file` (JSON), not a runtime knob.

## Verification

1. **Unit**: `tests/test_vault_abuse_exclusion.py` — **8 passed** (banter passthrough,
   per-category exclusion, audit provenance, idempotency, governance extension,
   Kestra outputs line); ruff clean on both touched files.
2. **Live Kestra validation**: POSTed the flow source to
   `POST /api/v1/flows/validate` on the production Kestra (127.0.0.1:8080) →
   **`valid: True`** (two deprecation notes on BOOLEAN inputs — identical style
   to the already-production `huible-onboard` flow).
3. **Deployed**: imported as **`huible/persona-vault` revision 1** (confirmed via
   flows/search; no trigger attached — no execution risk).
4. **Deterministic E2E smoke** (`use_llm=false` path) on a 5-row fixture:
   - V0: 4 chandler lines extracted (foreign speaker excluded)
   - V1: 4 atoms (0 removed)
   - V2 B3: **1 excluded (`dehumanization`), 3 kept** — exactly the abuse line;
     audit + report written; stats computed on the 3-line surviving corpus
   - V3 distill `--strict`: L0=3, L1=4, L2=1, `evidence_complete=True`
   - V3 structure `--no-llm`: 2 OKF docs written
   - V3 validate gate: **overall PASS (20/20 checks, exit 0)**
   - V5 wiring: `--list` OK (C1–C5 + K-controls battery printed, exit 0)
5. **V4 live gate**: `verify_tencentdb_memory.py` → **PASS: all layers returning
   data** (L0=5, L1=5, L2=9, L3=6).

## Residual

- None on this issue. Real-persona production runs of the flow go through the
  normal onboarding intake; V5 live battery stays opt-in by design (creates
  synthetic tickets).
- W6 openweight arm: tracked on [HU-2472](/HU/issues/HU-2472), gated on the
  board hosting decision — not a residual of the flow.
