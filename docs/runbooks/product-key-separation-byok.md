# Product Key Separation, Usage Metering & BYOK Runbook

HU-2243 (founder directive, Pat, 2026-08-30). Implements three parts:
dedicated product key, per-key-source usage metering, and per-tenant BYOK
vault. Ties to the M3 billing milestone (HU-2162).

## 1. Key separation

Internal workloads (board tooling, Jarvis bridge) keep the single trackable
board key (`jarvis-bridge-2026w35`, secret store). Persona/product voice
(Chandler et al.) runs on a dedicated provider key via the `PERSONA_LLM_*`
overlay:

```env
PERSONA_LLM_PROVIDER=      # zai | openrouter | gemini (empty = inherit shared LLM_*)
PERSONA_LLM_API_KEY=       # the dedicated product key
PERSONA_LLM_MODEL=
PERSONA_LLM_BASE_URL=
```

Empty overlay = today's posture (shared key). When set, the chat surface
builds its LLM client from the overlay and every usage row records
`key_source='product'`; shared-path turns record `key_source='shared'`.
Guardrail ledgers stay on the shared path for now.

## 2. Usage / billing metering

- `llm_usage` table (migration 004): one row per LLM call — persona,
  conversation, prompt/completion tokens, estimated cost, latency,
  `key_source` (migration 005), provider, model.
- `GET /api/usage/daily` — daily aggregate report (tokens, cost, calls)
  splittable by persona and `key_source`. This is the seed the M3 billing
  milestone (HU-2162) builds on.
- Recorder is best-effort: metering failure never fails a chat turn.

## 3. BYOK vault

Module: `src/huible/api/byok_vault.py`; table `byok_keys` (migration 006).

### Design

- **Envelope encryption.** `BYOK_VAULT_MASTER_KEY` derives a per-row
  AES-256-GCM key via scrypt (random 16-byte salt per row). The raw provider
  key is sealed with the caller's attribution id + provider as AES-GCM AAD —
  tampering with a row or moving it between tenants fails the decrypt.
  Stored format: `v1.<b64 salt>.<b64 nonce>.<b64 ciphertext+tag>`.
- **No raw keys in logs or API responses.** Endpoints return only a SHA-256
  `key_fingerprint` for confirmation.
- **Postgres backend** (sync, same posture as the safety DB) or in-memory
  for dev/test when no DB URL is configured.
- **Default-off.** No master key → no vault: endpoints 403, resolver skips
  the vault leg. `BYOK_ENABLED=false` disables the header hook entirely.

### Configuration

```env
BYOK_ENABLED=false           # gate for X-Provider-Key header hook
BYOK_VAULT_MASTER_KEY=       # openssl rand -hex 32; empty = vault disabled
```

Losing the master key invalidates all stored tenant keys (by design — no
escrow). Rotate by having tenants re-register.

### Turn resolution order

`_resolve_turn_llm` in `src/huible/api/app.py`:

1. `X-Provider-Key` request header (if `BYOK_ENABLED`)
2. Vault key registered for the caller's bearer-key digest
3. House key — dedicated product key if configured, else shared

Attribution is always the caller's own bearer-key digest;
`key_source='byok'` on vault/header turns. Any BYOK failure (bad key,
vault error) falls back to the house key — the turn still succeeds.

### Management endpoints

Require the vault to be enabled and the caller to hold the ops role:

- `PUT /api/byok/keys` — register/replace the caller's provider key
- `GET /api/byok/keys` — list fingerprints (never raw keys)
- `DELETE /api/byok/keys/{fingerprint}` — remove a key

## Operations checklist

1. Provision a dedicated provider key for product traffic (founder/board
   action — provider choice + payment), store it via the secret store, and
   set `PERSONA_LLM_API_KEY` (+ provider/model) in the deployment env.
2. Verify split: run product traffic, then check
   `GET /api/usage/daily?key_source=product` shows the turns and
   `key_source=shared` does not.
3. Enable BYOK only when clients ask for it: set `BYOK_ENABLED=true`; set
   `BYOK_VAULT_MASTER_KEY` only when durable tenant keys are needed.
4. Monitor `llm_usage` cost aggregates for the M3 billing milestone.

## Verification

- `tests/api/test_byok_vault.py` — vault crypto, tamper detection,
  endpoints, fallback
- `tests/api/test_byok_key_separation.py` — resolver order, key_source
  attribution, product/shared split
- `tests/api/test_usage_metering.py` — usage rows + daily aggregate
