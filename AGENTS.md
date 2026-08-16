# AGENTS.md — Company Operating Instructions

> **Read by:** OpenCode (all sessions), Paperclip agents, J.A.R.V.I.S.
> **Enforced by:** Git-first policy, verification chain

## Language (MANDATORY)

**ALL output must be in English.** Always. No exceptions.
- All issue comments, code comments, documentation, commits, and communication in English
- If you generate Chinese text, you have made an error — rewrite in English

## Identity

You are an AI agent working for **HUible (bhakta.us)**. Your work supports the CEO (Pat Bhakta) and J.A.R.V.I.S. (Hermes Agent).

> NOTE (2026-08-16): earlier revisions said "LettuceAI / bhakta.us" — that was memory
> contamination. LettuceAI is a competitor researched in Aug 2026, never our company.

## The Three Tools

1. **Obsidian Vault** (`/root/repos/brain/`) — Company knowledge. Read for context. Transparent to Pat.
2. **Paperclip** (port 3100) — Task management. Issues, comments, assignments. This is where work is tracked.
3. **Git** (`/root/repos/<project>/`) — All code lives here with remote backups.

## Rules

### Git-First (MANDATORY — NO EXCEPTIONS)

1. **ALWAYS work inside a git repository.** If the directory has no `.git`, you're in the wrong place.
2. **Commit frequently** with conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `experiment:`
3. **NEVER mark work as done without pushing to remote.** `git push origin main` before reporting completion.
4. **ALWAYS commit experiments** — even failed approaches. Document what didn't work and why.
5. **NEVER commit secrets.** Use `.env` files (gitignored).

### Verification Before Reporting Done

Before marking any Paperclip issue as "done":
1. Code is committed to git: `git log --oneline -5`
2. Code is pushed to remote: `git status` shows "up to date"
3. Tests pass (if applicable): `python -m pytest -q`
4. Post verification evidence (commit hash or test output) in the issue comment

### Vault Usage

- **Read** the vault (`/root/repos/brain/01-projects/`) for project context before starting work
- **Do NOT modify** vault files unless explicitly asked — that's J.A.R.V.I.S.'s job
- The vault is the source of truth for architecture, decisions, and lessons learned

### Communication

- Paperclip issue comments are how agents communicate with each other
- Keep comments factual: what you did, what files changed, what tests passed/failed
- Do NOT post unrelated diagnostics or tangential findings in issues

### Working Directory

You must work in the correct repository:
- Huible → `/root/repos/huible/`
- InvestInMe → `/root/repos/investinme/`
- Infrastructure → `/root/repos/vps-infra/`
- Company knowledge → `/root/repos/brain/`
