# HU-2774 — r8 confirmation battery readiness + Kestra topology correction (2026-09-09 ~18:45Z)

Trigger: CEO OPS comment (Kestra 2.0 MCP Server, vault ref JARVIS/kestra-mcp-server.md
@ 8400ba8): "once enabled, the dual-persona conversation loop and eval scorer should be
invocable by fleet agents as Kestra MCP tools instead of curl — use it if it unblocks the
loop faster; do not block the 3-run bar on it."

## Decision

MCP enablement is deferred until after the r8 bar. It cannot unblock the loop: the loop
is already armed as a Kestra one-shot cron (commit 5da8376). Touching Kestra config
before 00:30Z would add restart risk to shared infra for zero pre-bar benefit.

## r8 readiness — verified live 2026-09-09 ~18:40Z

| check | result |
| --- | --- |
| r8 flow registered | `pat.personas/dual-persona-confirmation-r8` present, trigger `one-shot-2026-09-10` cron `30 0 10 9 *` UTC **enabled** |
| runs on the right instance | legacy systemd Kestra **:8080** — the instance with all 22 prior `dual-persona-converse` executions (r5/r6/r7). Flow is NOT on kestra2 :8082 (pat.personas empty there) |
| script contract | `personas_dual_converse.py --turns 24 --run-id … --scenario friends/stranger --out-dir /root/repos/huible/runs/hu2774` matches flow commands; working tree == committed (d14a327 evaluator) |
| engine | huible-app healthy since 17:51:19Z; `/api/v1/health`: database ok, pgvector ok; chat path `LLM_PROVIDER=zai` (`state.llm_client`), `ZAI_DAILY_TOKEN_LIMIT=2000000`. Health string `generator: ready (mock)` refers to the VOICE generator (`make_generator_client`, provider unset → mock by design) — not the chat LLM |
| z.ai budget | `/var/lib/huible/zai-tokens.json` is date-keyed: `2026-09-09: 2,001,871` (exhausted, matches r7 evidence). Fresh 2M key at 2026-09-10T00:00Z; cron fires 00:30Z |
| disk | 26G free on / (78% used) — conversation JSONs are KB-scale |

## Kestra topology correction (report, per rule: report drift, don't edit JARVIS files)

JARVIS/kestra-mcp-server.md implies "our" flows live on kestra2 (:8082). Reality:

- **Legacy systemd `kestra.service` :8080 (v1.3.30)** — this is the ACTIVE company
  Kestra: 796 executions, `ops/agent-stall-watch` + `ops/ops-selftriage` on 30m
  schedules, and ALL pat.personas flows (dual-persona-converse, dual-persona-confirmation-r8).
  It was believed retired with the LiveSync stack (HU-1706); it is not. **Do not
  stop/retire it without migrating those flows** — that would kill tonight's r8 one-shot
  AND the ops monitors.
- **kestra2 container :8082 (v2.0.0)** — MCP-capable version, but zero pat.personas
  flows (only tutorials + pat.ingest/pat.inc probes). Config: /opt/kestra2/home-kestra/config.yml
  (mounted to /root/.kestra in-container). Nothing scheduled on it → restart-safe.
- **MCP enablement plan (post-bar, filed as child issue):** register
  `dual-persona-converse` + an eval/scorer entry flow on kestra2 (single registration —
  do NOT copy the r8 cron there before it fires on :8080, double-execution would burn
  the fresh 2M budget twice), add `kestra.server.mcp.enabled: true` to the kestra2
  config, `docker restart kestra2`, verify tools/list, then wire the fleet skill.
  Alternative: upgrade :8080 to 2.0 — rejected for now (v1.3.30→2.0 migration on live
  ops schedules is not a side quest for this ticket).
