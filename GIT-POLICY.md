# GIT-FIRST POLICY — BHAKTA.US / Paperclip System-Wide Standard

**Effective:** August 1, 2026
**Scope:** ALL projects — past, current, future. No exceptions.
**Authority:** Founder directive. This is a hard requirement, not a suggestion.

---

## THE RULE

**Every piece of code, scripts, configuration, or reproducible work product must live in a Git repository with a remote backup.**

If it's not in Git, it doesn't exist. If it's not pushed to a remote, it's at risk.

---

## WHY

1. **Audit trail** — We can see the full thinking process: what was tried, what worked, what didn't
2. **No repeated dead ends** — Future agents/humans can check git history before attempting an approach
3. **Reproducibility** — Anyone can clone and reproduce any work product
4. **Disaster recovery** — VPS can die; Git remotes survive
5. **Collaboration** — Multiple agents can work on the same codebase without conflicts

---

## STRUCTURE

### Central repository location
```
/root/repos/
├── GIT-POLICY.md          ← this file
├── investinme/             ← InvestInMe platform
├── lettuce/
│   ├── app/                ← LettuceAI app
│   ├── engine/             ← LettuceAI engine
│   ├── website/            ← LettuceAI website
│   ├── sprout/             ← Sprout backend
│   ├── seedvault/          ← SeedVault
│   ├── embeddings/         ← Embeddings service
│   ├── unified-entity-cards/
│   └── unified-system-cards/
├── huible/                 ← Huible persona engine
├── paperclip-config/       ← Paperclip agent configs, instructions, SOPs
├── vps-infra/              ← Server infrastructure, Caddy configs, systemd units
└── [project-name]/         ← New projects go here
```

### GitHub Organization
- **Org:** `LettuceAI` (already exists)
- New repos created under this org or a `bhakta-us` org as needed
- All repos **private** by default

---

## AGENT GIT WORKFLOW (MANDATORY)

Every Paperclip agent MUST follow this workflow when producing code:

### 1. Before starting work
```bash
cd /root/repos/<project>
git pull origin main
```

### 2. Create a feature branch
```bash
git checkout -b issue-<number>-<short-description>
# Example: git checkout -b issue-42-add-auth
```

### 3. Commit frequently (atomic commits)
```bash
git add -A
git commit -m "type(scope): description [issue-#]"
# Types: feat, fix, docs, refactor, test, chore, perf, ci
# Example: git commit -m "feat(auth): add JWT validation [issue-42]"
```

### 4. Push to remote (at minimum before completing a task)
```bash
git push origin <branch-name>
```

### 5. When task is complete
```bash
git checkout main
git merge <branch-name>
git push origin main
git branch -d <branch-name>
git push origin --delete <branch-name>
```

---

## COMMIT MESSAGE CONVENTION

Use Conventional Commits format:
```
type(scope): brief description [issue-#]

Optional longer description explaining what and why.
```

**Types:**
- `feat` — New feature
- `fix` — Bug fix
- `docs` — Documentation
- `refactor` — Code restructuring (no behavior change)
- `test` — Test additions/changes
- `chore` — Maintenance, deps, config
- `perf` — Performance improvement
- `ci` — CI/CD changes
- `experiment` — Experimental approach being tried (important for audit trail)

**The `experiment` type is critical** — when trying a new approach that might not work, commit it anyway with this type. Even if it fails, the commit history shows what was attempted and why it was abandoned.

---

## WHAT MUST BE IN GIT

| Type | Example | Repo |
|------|---------|------|
| Application code | React apps, APIs, engines | Project repo |
| Scripts | Deploy scripts, cron scripts, utilities | Project repo or vps-infra |
| Configuration | Caddyfile, systemd units, env templates | vps-infra |
| Agent instructions | AGENTS.md, SOUL.md, HEARTBEAT.md | paperclip-config |
| Skills/Playbooks | Hermes skills, agent skills | paperclip-config |
| Infrastructure | Dockerfiles, docker-compose | Project repo or vps-infra |
| Documentation | READMEs, SOPs, architecture docs | Project repo |
| Notebooks | Jupyter notebooks, experiment logs | Project repo |

## WHAT SHOULD NOT BE IN GIT

- Secrets (API keys, passwords, tokens) — use `.env` files (gitignored)
- Database dumps
- `node_modules/`, `__pycache__/`, build artifacts
- Large binary files (>10MB) — use Git LFS or external storage

---

## ENFORCEMENT

1. **Agent instructions** — Every agent's AGENTS.md includes the Git Workflow section
2. **Cron enforcer** — A scheduled job checks that workspaces have git repos and are pushed
3. **Code review** — No issue can be marked "done" unless the code is committed and pushed
4. **Pre-task check** — Agents must verify they're working in a git repo before writing any code

---

## LEGACY MIGRATION

All existing code must be migrated into this structure:
1. Identify all code on the system
2. Move to appropriate directory under `/root/repos/`
3. Ensure git init + remote configured
4. Push to GitHub
5. Update symlinks/references

---

## QUICK REFERENCE

```bash
# Initialize a new project repo
cd /root/repos
mkdir <project-name> && cd <project-name>
git init
echo "# Project Name" > README.md
cp /root/repos/GIT-POLICY.md .  # Include policy in every repo
git add -A && git commit -m "chore: initial commit"
gh repo create LettuceAI/<project-name> --private --source=. --push
```
