# R&D Eval: DeepSeek Harness (dsh) vs opencode as Paperclip execution adapter

Date: 2026-08-17 · Issue: HU-1828 · Agent: R&D Lead · Budget: one session (as constrained)
Sandbox: `experiments/2026-08-17-dsh-eval/` (upstream + fork shallow clones, crg venv; clones/venv gitignored, this file + `zai.patch.yml` committed)

## TL;DR

| Question | Answer |
| --- | --- |
| Replace opencode with dsh as Paperclip adapter? | **No — now.** Exit-code/stdout contract works, but no `--format json`, no headless session resume, dev-preview churn. Wrong risk profile for the company backbone. |
| Adopt fork's `code-review-graph` into our existing opencode flow? | **Yes — cheapest win.** Standalone PyPI MCP server; 4.4s build, 21MB DB, 0.5s incremental, 30 MCP tools verified against huible. Harness-independent. |
| Hybrid? | Effectively yes, but inverted from the issue's framing: keep **opencode as the Paperclip backbone** and lift **dsh's graph layer** into it. dsh itself stays an R&D watchlist item. |
| zai/GLM routing in dsh? | **Proven end-to-end.** pi-ai ships a native `zai` catalog route (`api.z.ai/api/coding/paas/v4`); 10-line overlay ran GLM-4.7 headless: exit 0, clean stdout. |
| Session-store hygiene | **Much lighter than opencode's SQLite.** Per-session zstd JSONL files (~23KB/run vs opencode's ~370KB/session DB average that hit 4.5GB/13d). No shared DB, no vacuum bloat, prune = rm session dirs. No built-in retention — needs the same external timer pattern we already run for opencode. |

## 1. Build & smoke test (task 1)

- Clones: `deepseek-ai/deepseek-harness` (upstream) + `rpmalouin/deepseek-harness` (fork, tracks upstream master @ `47f943859bef`, 2026-08-13). Fork delta: ~264 added files, 12 modified — purely additive (MCP registrations, skills, agent-rules files, hooks) as FORK.md claims.
- Build (fork): `pnpm install --frozen-lockfile && pnpm run build` → **EXIT 0** (~6 min, 8-core VPS). Node 24.19 / pnpm 11.20 both within engines range.
- Headless one-shot smokes:
  - `dsh --profile headless "Reply with exactly: PONG"` w/o key → **fail-loud verified**: `MISSING_CREDENTIAL` on stderr, exit 1 (exact documented contract).
  - With `--patch zai.patch.yml` → `PONG` on stdout, stderr empty, **exit 0**, 14.4s wall (incl. tsx source-launch + plugin-tree boot; boot overhead ~10s).
  - Tool-loop task (create file, verify content, reply) → file created correctly, `WROTE`, exit 0, 16.6s.

## 2. Paperclip adapter compatibility (task 2)

opencode contract today: `opencode run --format json --session <id>` (structured messages + session continuity).
dsh headless contract: `pnpm dsh --profile headless [--patch yml] "<task>"` — **plain-text stdout** (last non-empty assistant message), stderr empty on success, exit 0 = completed / 1 = otherwise.

Friction matrix:

| Capability | opencode | dsh headless | Impact |
| --- | --- | --- | --- |
| Structured output (`--format json`) | yes | **no** (plain text only) | Adapter must parse prose; no message-level metadata (tool calls, usage) on stdout |
| Session resume/continuation | `--session <id>` | **no** — headless creates a fresh Agent per run; "one submitted task only", no follow-up surface (resume exists only in TUI/web) | Heartbeat-style iterative work loses context; Paperclip's graceful-fresh-session retry pattern becomes the *only* mode |
| Exit-code contract | process exit | **0 = completed, 1 otherwise** — clean, better than opencode's | Good |
| Config overlays | config file | `--patch <yml>` repeatable runtime overlays; `--dump-config` for composition introspection | Good (arguably better) |
| Source launch | installed binary | `pnpm dsh` tsx source-launch (no rebuild) or built lib | Slower cold start (~10s boot observed) |
| Maturity | stable, our 13d prod history | **dev preview, "THERE WILL BE COMPATIBILITY-BREAKING CHANGES"** (their README), 0.1.0-rc.5, `SESSION_FORMAT_VERSION 0` with no compat promise | Disqualifying for backbone use *today* |

Verdict: shell-out integration is *possible* (exit-code + stdout is a parseable contract, Hermes does exactly this), but it is a **downgrade** on the two axes Paperclip actually uses (JSON messages, session continuity) with a dev-preview risk multiplier.

## 3. Graph layer standalone (task 3) — the cheap win, verified

- `code-review-graph` (PyPI, `tirth8205/code-review-graph`, v2.3.7 installed) is fully standalone: no dsh, no Cordis, no DeepSeek anything.
- MCP stdio server (`python3 -m code_review_graph serve`) verified with raw JSON-RPC: initialize handshake OK, **30 tools** (query_graph, impact_radius, detect_changes, affected_flows, communities, architecture_overview, …).
- Built against **huible itself**: 215 files → 3,037 nodes / 21,265 edges in **4.4s**; DB **21MB** (`.code-review-graph/` in repo root, gitignored per fork convention).
- Incremental update after 1-file touch: **0.49s**. Hook-driven auto-update (PostToolUse) is the fork's steady-state pattern and is cheap enough for our heartbeats.
- Caveat: `semantic_search_nodes_tool` needs the separate `embed` step (vector embeddings, optional provider) — structural tools (callers/impact/flows/review-context) are fully keyless.

Adoption path for opencode: install once in a venv, register in opencode's MCP config (the fork's `.mcp.json` shows the exact stdio shape), add graph-first wording to AGENTS.md. No harness switch required.

## 4. Session-store hygiene (task 4)

dsh: per-session append-only JSONL, **zstd-compressed**, at `~/.dsh/sessions/--<normalized-cwd>--/session-<uuid>/session.jsonl.zstd` (JSONL backend default; SQLite backend also exists).

Measured: 3 sessions (2 real LLM runs + 1 failed-cred run) → 22–24KB compressed each (~65KB raw, 25 events for a PONG run); total `~/.dsh` = 1.6MB after boots.

Comparison at opencode's incident scale (12.3K sessions/13d):
- opencode SQLite: reached **4.5GB** (~370KB/session avg; needed JARVIS's prune to 1.2GB + weekly timer + 3GB alert).
- dsh JSONL+zstd at same volume: extrapolates to **~280MB**, and — more importantly — **no shared event DB**: no vacuum bloat, no lock contention, pruning is `rm -rf` of session dirs, and a runaway session can't degrade a shared store.
- Gap: **no built-in retention/prune/GC** found in the session packages — same external systemd-timer pattern we already run for opencode would be required. (SQLite backend exists too; not evaluated in depth — JSONL is default.)

## 5. Model routing to zai/GLM (task 5) — proven, not just documented

- `llm-pi-ai` adapter: hand-declared OpenAI-compatible routes (`api`, `baseURL`, `apiKeyEnv`, custom `models`) are first-class config; z.ai's `thinking` dialect is explicitly supported (`compat.thinkingFormat`).
- Stronger: pi-ai 0.82.1's **installed catalog ships `zai` natively** (`https://api.z.ai/api/coding/paas/v4`, models glm-4.7 / 5.1 / 5.2 / 5.2-1M-ctx …), so zai is a *catalog route*: credential reference only, no baseURL/models needed.
- Empirical: `zai.patch.yml` (10 lines, committed here) + existing `GLM_API_KEY` → headless run on **glm-4.7**, exit 0, clean output. Cost-discipline posture (GLM-primary) is fully compatible with dsh, contrary to the fork's OpenRouter-default framing.
- OpenRouter also available for fallback exactly as FORK.md documents (`OPENROUTER_API_KEY` present on this VPS).

## 6. Recommendation matrix (task 6)

| Option | Verdict | Why |
| --- | --- | --- |
| **opencode-only (status quo)** | Acceptable baseline | Known, stable, session continuity, JSON output; but leaves the 4.5GB-class store risk managed only by pruning, and no graph layer |
| **dsh-only (switch backbone)** | **Reject for now** | Dev preview + breaking-change promise; no `--format json`; no headless resume; would forfeit 13d of opencode operational lessons; graph benefits are portable without dsh |
| **Hybrid as framed (dsh for code-heavy R&D, opencode for Paperclip ops)** | Reject the *harness* half | Running two agent harnesses doubles security/update surface; dsh's unique benefit (graph) is adoptable standalone; one-shot `pnpm dsh` per-task runs remain available ad hoc in experiments if ever needed |
| **Hybrid inverted (opencode backbone + dsh's graph layer)** | **Recommend** | Keeps the stable adapter; imports the genuinely novel, cheap, verified win; zero new agent-runtime risk |

Re-evaluate dsh as adapter when: first tagged stable release, headless gains structured output (or a JSON-RPC/ACP automation path matures — `dsh-acp` exists and is worth a re-look), and session-continuity story for headless lands. Watchlist, not adoption.

## Artifacts

- `zai.patch.yml` — working zai/GLM routing overlay (committed)
- This report: `experiments/2026-08-17-dsh-eval/REPORT.md` (committed); full version also on the issue as document `research`
- Upstream/fork clones + crg venv live in the sandbox dir (gitignored; reproducible via FORK.md + PyPI)
