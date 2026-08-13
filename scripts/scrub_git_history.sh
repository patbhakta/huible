#!/usr/bin/env bash
# Git history scrubber — HU-1503 defense-in-depth step.
#
# Context: the CouchDB admin password (the "b756e723…" literal) was hardcoded in
# version-controlled files on the PUBLIC repo patbhakta/huible (HU-1435 finding).
# HU-1500 removed it from the working-tree tip (af6a70c) and ROTATES the live
# credential so the exposed value is neutralized. But the literal remains in
# public git history forever — rotation makes it harmless, this script removes
# the long-tail surface.
#
# This is the THIRD step in the HU-1501 recovery trio:
#   1. scripts/verify_vps_recovery.sh   → proves the VPS + services are back
#   2. scripts/rotate_couch_admin_pass.sh → kills the exposed credential (HU-1500)
#   3. scripts/scrub_git_history.sh     → THIS: purges the literal from history
# Run AFTER rotation is confirmed dead. Scrubbing before rotation is pointless —
# it would only hide a value that is still live.
#
# When to run: once HU-1500 is `done` (old value confirmed 401). NOT a launch
# gate (priority medium), but the natural moment is the recovery window while
# the incident is fresh and clones/forks are few.
#
# Safety properties:
#   - SECRET-SAFE: never prints the target literal. Only its length and a
#     sha256 prefix are logged, purely so the operator can confirm THIS script is
#     targeting the same value HU-1500 rotated. The full transcript is safe to
#     paste into the HU-1503 issue as evidence.
#   - NON-DESTRUCTIVE BY DEFAULT: without ROTATION_CONFIRMED=1 it only plans
#     (DRY_RUN); without PUSH=1 it rewrites local history but does NOT push, so
#     origin is untouched until an operator explicitly opts in.
#   - BACKUP-FIRST: writes a full .bundle of all refs + a refs/backups/* branch
#     per head BEFORE any rewrite, so the operation is reversible.
#   - CLEAN-TREE GUARD: refuses to rewrite with uncommitted changes.
#   - POST-CHECK: proves `git log --all -p -S <literal>` is empty afterwards —
#     that is HU-1503 acceptance criterion #1.
#
# Tool selection (auto):
#   - Prefers `git filter-repo` (recommended, fast, replaces across all blobs).
#   - Falls back to the built-in `git filter-branch` when
#     ALLOW_FILTER_BRANCH=1 (portable, no install, but slow on large repos).
#   - If neither path is available/refused, exits with a plan + install hint.
#   - If filter-repo is missing, install it once:
#       pip install git-filter-repo      # or: apt install git-filter-repo
#
# Usage (from the repo root of a FRESH clone, post-rotation):
#   # Supply the literal via a 0600 file so it never enters shell history:
#   # NEVER paste the literal here. Get it from the HU-1500 runbook doc, then:
#   umask 077; printf '%s' "$(cat /run/secrets/couch_old_pass)" > /tmp/lit.txt
#   SECRET_LITERAL_FILE=/tmp/lit.txt bash scripts/scrub_git_history.sh          # plan
#   ROTATION_CONFIRMED=1 SECRET_LITERAL_FILE=/tmp/lit.txt \
#     bash scripts/scrub_git_history.sh                                        # rewrite local
#   ROTATION_CONFIRMED=1 PUSH=1 SECRET_LITERAL_FILE=/tmp/lit.txt \
#     bash scripts/scrub_git_history.sh                                        # rewrite + force-push
#
# Exit codes: 0 only if the chosen scope fully succeeds and the post-check
# passes; non-zero with a RESULT line otherwise. PUSH=1 failures are reported
# but do NOT undo a successful local rewrite (re-run with PUSH=1).
set -u

REPLACEMENT="${REPLACEMENT:-***REMOVED***}"
ROTATION_CONFIRMED="${ROTATION_CONFIRMED:-0}"
ALLOW_FILTER_BRANCH="${ALLOW_FILTER_BRANCH:-0}"
DRY_RUN="${DRY_RUN:-0}"
PUSH="${PUSH:-0}"
SECRET_LITERAL="${SECRET_LITERAL:-}"
SECRET_LITERAL_FILE="${SECRET_LITERAL_FILE:-}"

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1 — $2"; FAIL=$((FAIL+1)); }
note() { echo "       $1"; }
die()  { echo "RESULT: $1"; echo "=== Summary: $PASS passed, $FAIL failed ==="; exit 1; }

echo "=== Git history scrubber (HU-1503 defense-in-depth) ==="
echo "repo: $(pwd)  time: $(date -u +%FT%TZ)  tool-pref: $([ -n "$(command -v git-filter-repo || command -v git filter-repo 2>/dev/null)" ] && echo filter-repo || echo auto)"
echo

# ─── Resolve the target literal without ever printing it ─────────────────────
if [ -z "$SECRET_LITERAL" ] && [ -n "$SECRET_LITERAL_FILE" ]; then
  [ -f "$SECRET_LITERAL_FILE" ] || die "SCRUB_FAILED — SECRET_LITERAL_FILE not found: $SECRET_LITERAL_FILE"
  SECRET_LITERAL="$(cat "$SECRET_LITERAL_FILE")"
fi
if [ -z "$SECRET_LITERAL" ]; then
  die "SCRUB_FAILED — no target literal. Set SECRET_LITERAL or SECRET_LITERAL_FILE (a 0600 file is safest)."
fi
[ "${#SECRET_LITERAL}" -ge 8 ] || die "SCRUB_FAILED — literal too short (${#SECRET_LITERAL} chars); refusing near-wildcard targets."
# Identity fingerprint (length + sha256 prefix) so the operator can confirm this
# is the same value HU-1500 rotated. The value itself is never echoed.
LIT_LEN="${#SECRET_LITERAL}"
LIT_HASH="$(printf '%s' "$SECRET_LITERAL" | sha256sum | cut -c1-16)"
note "target literal: ${LIT_LEN} chars, sha256:${LIT_HASH} (value redacted from transcript)."

# ─── Repo + clean-tree guards ────────────────────────────────────────────────
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "SCRUB_FAILED — not inside a git work tree."
[ "$(git rev-parse --show-toplevel)" = "$(pwd)" ] || die "SCRUB_FAILED — run from the repo root ($(git rev-parse --show-toplevel))."
if [ -n "$(git status --porcelain)" ]; then
  fail "Working tree dirty" "commit or stash changes first; history rewrite requires a clean tree."
  die "SCRUB_FAILED — uncommitted changes present."
fi
ok "Repo is a git work tree at root with a clean working tree."

# ─── Scope: is the literal even in history? ──────────────────────────────────
hit_commits="$(git log --all --oneline -S "$SECRET_LITERAL" | wc -l | tr -d ' ')"
hit_files="$(git log --all -p -S "$SECRET_LITERAL" --format= 2>/dev/null | grep -E '^diff --git' | sort -u | wc -l | tr -d ' ')"
if [ "$hit_commits" -eq 0 ]; then
  ok "Literal not present in any reachable history — nothing to scrub."
  echo "RESULT: HISTORY_CLEAN (no rewrite needed). Acceptance #1 already satisfied."
  echo "=== Summary: $PASS passed, $FAIL failed ==="
  exit 0
fi
ok "Literal found in history: ${hit_commits} commit(s), ${hit_files} file path(s) across all refs."
git log --all -p -S "$SECRET_LITERAL" --format= 2>/dev/null | grep -E '^diff --git' | sort -u | sed 's/^diff --git a\//       file: /; s/ b\/.*//' | head -50
echo

# ─── Rotation gate ───────────────────────────────────────────────────────────
if [ "$ROTATION_CONFIRMED" != "1" ]; then
  echo "## Rotation gate"
  fail "Rotation not confirmed" "set ROTATION_CONFIRMED=1 only after HU-1500 proves the old value 401s."
  echo
  echo "RESULT: PLAN_ONLY (ROTATION_CONFIRMED not set). No history was rewritten."
  note "Rotation (HU-1500) MUST complete first — scrubbing only hides a still-live credential."
  note "Once rotation is verified: ROTATION_CONFIRMED=1 ... bash scripts/scrub_git_history.sh"
  echo "=== Summary: $PASS passed, $FAIL failed ==="
  exit 0
fi
ok "Rotation confirmed by operator (ROTATION_CONFIRMED=1) — scrub may proceed."
note "Reminder (acceptance #3): after scrub, re-confirm the old value still 401s — rotation stays in force regardless of history."

# ─── Effective mode ──────────────────────────────────────────────────────────
[ "$DRY_RUN" = "1" ] && ROTATION_CONFIRMED=0   # DRY_RUN always wins → plan only
if [ "$ROTATION_CONFIRMED" != "1" ]; then
  echo "## Mode: PLAN ONLY (DRY_RUN=1)"
  note "Would rewrite all refs, replacing the literal with '${REPLACEMENT}'."
  note "Rerun without DRY_RUN=1 (and ROTATION_CONFIRMED=1) to rewrite; add PUSH=1 to also force-push."
  echo "RESULT: PLAN_ONLY (DRY_RUN). No history rewritten."
  echo "=== Summary: $PASS passed, $FAIL failed ==="
  exit 0
fi
echo "## Mode: REWRITE$([ "$PUSH" = "1" ] && echo ' + FORCE-PUSH')"
echo

# ─── Backup before any rewrite (reversible) ──────────────────────────────────
backup_dir="$(pwd)/.git/scrub-backups"
mkdir -p "$backup_dir"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
bundle="$backup_dir/pre-scrub-${ts}.bundle"
if git bundle create "$bundle" --all >/dev/null 2>&1; then
  ok "Full bundle backup: $bundle"
else
  fail "Backup bundle failed" "git bundle create exited non-zero — aborting before rewrite."
  die "SCRUB_FAILED — could not create safety backup."
fi
# Also pin each head under refs/backups/* as a second recovery path.
for ref in $(git for-each-ref --format='%(refname)' refs/heads); do
  name="${ref#refs/heads/}"
  git update-ref "refs/backups/pre-scrub-${ts}/${name}" "$(git rev-parse "$ref")" 2>/dev/null && PASS=$((PASS+1))
done
note "Heads also pinned under refs/backups/pre-scrub-${ts}/* (restore: git reset --hard <that ref>)."
echo

# ─── Tool selection ──────────────────────────────────────────────────────────
echo "## Selecting rewrite tool"
# Secret-safe replacements file (0600): one line "<literal>==><replacement>".
repl_dir="$(mktemp -d "${TMPDIR:-/tmp}/.scrub.XXXXXX")"; chmod 700 "$repl_dir"
cleanup_repl() { rm -rf "$repl_dir"; }
trap cleanup_repl EXIT
repl_file="$repl_dir/replacements.txt"
# filter-repo --replace-text format: "old==>new". The literal lands in a 0600
# file (already public per the premise, but kept out of transcripts/logs).
printf '%s==>%s\n' "$SECRET_LITERAL" "$REPLACEMENT" > "$repl_file"; chmod 600 "$repl_file"
# filter-branch fallback needs the literal and replacement as SEPARATE 0600
# files: its tree-scrub helper reads RAW values, not the "old==>new" filter-repo
# format. Passing replacements.txt there (and a never-created repl.txt) made the
# fallback a silent no-op that then died at the post-check.
lit_file="$repl_dir/literal.txt"; printf '%s' "$SECRET_LITERAL" > "$lit_file"; chmod 600 "$lit_file"
repl_only="$repl_dir/replacement.txt"; printf '%s' "$REPLACEMENT" > "$repl_only"; chmod 600 "$repl_only"

has_filter_repo=0
if command -v git-filter-repo >/dev/null 2>&1; then
  has_filter_repo=1
elif git filter-repo --version >/dev/null 2>&1; then
  has_filter_repo=1
fi

if [ "$has_filter_repo" = "1" ]; then
  ok "Using git filter-repo --replace-text (recommended)."
  echo
  echo "## Rewriting all refs"
  if git filter-repo --replace-text "$repl_file" --force; then
    ok "filter-repo rewrite completed."
  else
    fail "filter-repo failed" "exit non-zero; history may be partially rewritten — restore from $bundle before retrying."
    die "SCRUB_FAILED — filter-repo error."
  fi
elif [ "$ALLOW_FILTER_BRANCH" = "1" ]; then
  ok "git filter-repo unavailable — using built-in git filter-branch (ALLOW_FILTER_BRANCH=1)."
  note "filter-branch tree-filter checks out every commit — slow on large repos but correct."
  # Helper that does the in-place replacement on the checked-out tree. The
  # literal is read from a 0600 file, never embedded in the helper source and
  # never echoed.
  helper="$repl_dir/tree_scrub.sh"
  cat > "$helper" <<'HELPER'
#!/usr/bin/env bash
set -u
LIT_FILE="$1"; REPL_FILE="$2"
[ -f "$LIT_FILE" ] || exit 0
LIT="$(cat "$LIT_FILE")"; REPL="$(cat "$REPL_FILE")"
[ -n "$LIT" ] || exit 0
# Literal is pure hex (no regex metacharacters); perl \Q..\E guards anyway.
git ls-files -z | while IFS= read -r -d '' f; do
  if [ -f "$f" ] && grep -IqF -- "$LIT" "$f" 2>/dev/null; then
    LIT="$LIT" REPL="$REPL" perl -i -pe 's/\Q$ENV{LIT}\E/$ENV{REPL}/g' "$f"
  fi
done
HELPER
  chmod 700 "$helper"
  export FILTER_BRANCH_SQUELCH_WARNING=1
  if git filter-branch --force --prune-empty --tree-filter "bash '$helper' '$lit_file' '$repl_only'" --tag-name-filter cat -- --all; then
    ok "filter-branch rewrite completed."
  else
    fail "filter-branch failed" "exit non-zero; restore from $bundle before retrying."
    die "SCRUB_FAILED — filter-branch error."
  fi
  # filter-branch leaves refs/original/* pointing at the PRE-rewrite commits.
  # The post-check uses `git log --all`, which traverses those, so without this
  # cleanup the fallback ALWAYS reports SCRUB_INCOMPLETE even when every real
  # branch is clean. The bundle + refs/backups/* remain as recovery paths.
  orig_count=0
  while IFS= read -r _oref; do
    git update-ref -d "$_oref" 2>/dev/null && orig_count=$((orig_count+1))
  done < <(git for-each-ref --format='%(refname)' refs/original/)
  [ "$orig_count" -gt 0 ] && note "Dropped $orig_count filter-branch backup ref(s) under refs/original/ (pre-rewrite copies) so the post-check is accurate."
else
  echo
  echo "RESULT: PLAN_ONLY — no rewrite tool available."
  note "Recommended: pip install git-filter-repo  (or: apt install git-filter-repo), then rerun."
  note "Portable fallback: rerun with ALLOW_FILTER_BRANCH=1 to use the built-in git filter-branch."
  note "No history was changed. Backup bundle at $bundle (safe to keep or delete)."
  echo "=== Summary: $PASS passed, $FAIL failed ==="
  exit 0
fi
echo

# ─── POST-CHECK: HU-1503 acceptance #1 — literal gone from all history ───────
echo "## Post-check (acceptance #1: literal absent from all refs)"
remaining="$(git log --all -p -S "$SECRET_LITERAL" --format= 2>/dev/null | grep -c -- "$SECRET_LITERAL")"
if [ "$remaining" -eq 0 ]; then
  ok "git log --all -p -S <literal> → no matches in any ref. Acceptance #1 SATISFIED."
else
  fail "Literal still present" "${remaining} occurrence(s) remain in history after rewrite — manual review needed."
  note "Restore from $bundle and retry with git filter-repo if this persists."
  die "SCRUB_INCOMPLETE — literal still reachable."
fi
echo

# ─── Force-push (opt-in) — acceptance #2 ─────────────────────────────────────
echo "## Publish (acceptance #2: all branches + tags force-pushed to origin)"
if [ "$PUSH" != "1" ]; then
  note "PUSH not set — local rewrite only. Publish manually when ready:"
  note "  git push --force-with-lease origin --all && git push --force-with-lease origin --tags"
  note "Then: GitHub may need a Support request to purge cached refs (note in HU-1503)."
  echo
  echo "RESULT: REWRITTEN_LOCALLY (not pushed). Backup: $bundle"
  echo "=== Summary: $PASS passed, $FAIL failed ==="
  exit 0
fi
remote="$(git remote get-url origin 2>/dev/null || true)"
[ -n "$remote" ] || { fail "No origin remote" "cannot force-push."; die "PUSH_FAILED — no origin."; }
ok "origin remote present: $remote"
if git push --force-with-lease origin --all >/dev/null 2>&1; then
  ok "Force-pushed all branches (--force-with-lease)."
else
  fail "Branch force-push failed" "someone advanced origin since backup — resolve by hand, do NOT blind --force."
  note "Local rewrite is intact; re-run with PUSH=1 after reconciling, or push per-branch."
  die "PUSH_FAILED — branch push rejected (use --force-with-lease conflict resolution)."
fi
# Tags (rewrite touches any tag pointing at rewritten commits). Best-effort.
if [ -n "$(git tag)" ]; then
  if git push --force-with-lease origin --tags >/dev/null 2>&1; then
    ok "Force-pushed all tags."
  else
    note "Tag push failed or no upstream tags — review manually if tags existed."
  fi
else
  note "No tags to push."
fi
echo

echo "RESULT: SCRUB_COMPLETE (rewritten + pushed). Backup bundle: $bundle"
note "Acceptance #3: re-confirm HU-1500 rotation holds (old value still 401) — rotation is independent of history."
note "Anyone with an old clone must re-clone; request GitHub Support purge cached refs if needed."
echo "=== Summary: $PASS passed, $FAIL failed ==="
exit 0
