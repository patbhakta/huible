# HUible moat console (HU-2793)

Founder-visible memory moat console. Astro 7, `output: "server"`, Node adapter, tailnet-only.

This repo slice is the **read-only API lane** (Tech Lead). The UI lane (Design Technologist) ships the founder-facing pages on these endpoints:

- `/` — console overview + gateway health
- `/:tenant/chat` — live chat: real engine path, G6 consent card, verbatim X-ray per turn, amnesia test, W4 kill switch (HU-2921)
- `/:tenant/memory` — L0 conversation + L1 profile memory search
- `/:tenant/xray` — X-ray recall probe (renders the exact injected block verbatim)
- `/:tenant/vault` — vault browser (`?sub=` listing, `?rel=` note reader)

Zero client JS: plain GET forms, server-rendered. Sepia/newspaper Monkey47 identity per `huible-frontend/THEME.md` (self-hosted fonts in `public/fonts/`, OFL licenses included).

## Run

```sh
npm install
npm run build
npm run serve   # binds HUIBLE_MOAT_HOST/port from astro.config.mjs (100.101.235.117:8100)
```

## Deploy (systemd, HU-2921)

The console is the **ONE founder URL**: `http://100.101.235.117:8100` (tailnet-only), unit `huible-moat-console.service`:

```ini
[Service]
WorkingDirectory=/root/repos/huible/moat-console
EnvironmentFile=/root/.hermes/.env   # HUIBLE_DEMO_KEY, HUIBLE_DEMO_KEY_MONICA (persona-scoped engine keys)
ExecStart=/root/.nvm/versions/node/v24.19.0/bin/node ./dist/server/entry.mjs
Restart=on-failure
```

Deploy = `npm run build && systemctl restart huible-moat-console.service`.

The old v1 demo server (`scripts/moat_demo_server.py`) is **retired**: `huible-moat-demo.service` now runs `scripts/moat_8097_redirect.py`, which redirects every :8097 hit to `:8100/huible-demo/chat` (302 GET / 307 other verbs).

## Chat lane (HU-2921) — orchestration via the engine, never the store

| Endpoint | Method | Notes |
| --- | --- | --- |
| `/api/:tenant/chat/session` | POST | mints a `moat-*` conversation id after an engine health check |
| `/api/:tenant/chat/turn` | POST | `{message, conversationId, workingMemoryEnabled?}` → proxies the **real engine** `/chat/:persona`; engine status passes through verbatim (200 reply+trace, 409 G6 consent card, 429 G8 dosage cap) |
| `/api/:tenant/chat/consent` | POST | `{conversationId}` → acknowledges the real G6 gate |

Read-only invariant holds: the console still never writes to TencentDB or the vault directly — chat orchestration goes through the engine (which owns capture/recall), and the memory/vault endpoints stay GET/search-only. `workingMemoryEnabled: false` = W4 kill switch: the engine skips the working-memory lane for that single request (visible X-ray delta: the injected block disappears; vault lanes stay on).

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
- Chat + amnesia + kill-switch orchestration shipped HU-2921 (commit `6344a7d` + this slice): `/:tenant/chat` proxies the real engine with trace + `working_memory_enabled`.
