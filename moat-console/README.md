# HUible moat console (HU-2793)

Founder-visible memory moat console. Astro 7, `output: "server"`, Node adapter, tailnet-only.

This repo slice is the **read-only API lane** (Tech Lead). UI lane (Design Technologist) builds on these endpoints.

## Run

```sh
npm install
npm run build
npm run serve   # binds HUIBLE_MOAT_HOST/port from astro.config.mjs (100.101.235.117:8100)
```

## API (all read-only)

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/api/health` | GET | console + gateway status |
| `/api/:tenant/memory/recall` | POST | `{query, conversationId \| sessionKey}` → exact Arm A block the engine would inject (v4 digest + excerpts) |
| `/api/:tenant/memory/conversations` | POST | `{query, limit?, conversationId?/sessionKey?}` → L0 rows; cross-tenant rows dropped, count reported |
| `/api/:tenant/memory/memories` | POST | `{query, limit?, type?, scene?}` → L1 memories (user-profile scoped on this gateway instance) |
| `/api/:tenant/vault/notes` | GET | `?sub=` dir listing (markdown notes + dirs) |
| `/api/:tenant/vault/note` | GET | `?rel=` note body; path-validated, `.md` only |

## Tenants

`src/lib/tenants.ts` — slug → `{personaId, serviceId, vaultRoot}`:

- `huible-demo` → Chandler (`huible-chandler`, `/root/repos/personas/chandler-bing/vault`)
- `huible-monica` → Monica (`huible-monica`, `/root/repos/personas/monica/vault`)

Tenant isolation: every gateway call carries the tenant's `x-tdai-service-id` header (the same partitioning the engine uses) and session keys are namespaced `huible-p<personaId>-c<conversationId>`. L0 search results are additionally post-filtered to the tenant persona. No write paths exist here: the console never calls `/capture`, `/seed`, `/session/end`, or any admin route.

Env: `HUIBLE_WM_GATEWAY` (default `http://127.0.0.1:8420`), `HUIBLE_WM_GATEWAY_KEY` (optional Bearer for the gateway).

## UI lane notes

- Component-agnostic: bring the sepia/Monkey47 identity; the API returns plain JSON + pre-formatted gateway result text.
- The recall endpoint returns exactly what the engine injects per turn — render it verbatim (X-ray pane contract, frozen scope 2026-09-14).
- Chat + amnesia + kill-switch orchestration (proxying the real engine chat with trace + `working_memory_enabled`) is the next Tech Lead slice; see HU-2793.
