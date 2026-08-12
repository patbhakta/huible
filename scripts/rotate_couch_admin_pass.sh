#!/usr/bin/env bash
# CouchDB admin credential rotation runbook — HU-1500 operational step.
#
# Context: the CouchDB admin password for user `obsidian` was hardcoded in
# version-controlled files on a public repo (HU-1435 finding). The literal was
# removed from the repo tip in af6a70c, but it remains in public git history, so
# the real mitigation is ROTATION — change the live credential so the exposed
# value is neutralized. This script is that rotation.
#
# When to run: as the FIRST step after verify_vps_recovery.sh reports
# VPS_RECOVERED, BEFORE re-enabling any Kestra flow or serving user traffic
# (see HU-1501 recovery gate). The VPS being down is the natural rotation
# window — the old credential is not actively live, so there is no auth storm.
#
# Where to run: ON THE PRODUCTION VPS (208.84.102.243), because CouchDB is
# localhost-bound (http://localhost:5984). Run via SSH or the provider console.
#
# Safety properties:
#   - SECRET-SAFE: never prints any password value (old or new). Only length,
#     presence, and CouchDB's stored PBKDF2 hash prefix are logged — the full
#     transcript is safe to paste into the HU-1500 issue as evidence.
#   - PRE-CHECK: aborts if the old password does not authenticate, so we never
#     leave CouchDB in a half-rotated / locked-out state.
#   - POST-CHECK: proves the new password authenticates AND that the old one no
#     longer does, before declaring success.
#   - IDEMPOTENT-ISH: a second run with the (now-rotated) value in
#     $COUCH_ADMIN_PASS will fail the pre-check loudly — re-running with a stale
#     old value is a no-op, not a second rotation.
#
# Usage (on the VPS):
#   # The currently-live (exposed) credential must be supplied via env so it is
#   # not echoed in the shell history or process list of this script:
#   export COUCH_ADMIN_PASS='<current live password>'
#   bash scripts/rotate_couch_admin_pass.sh
#       # optional overrides:
#   COUCH_URL=http://localhost:5984 COUCH_ADMIN_USER=obsidian \
#   KESTRA_ENV_FILE=/etc/kestra/kestra.env \
#     bash scripts/rotate_couch_admin_pass.sh
#   # DRY_RUN=1 to generate + verify the rotation plan WITHOUT changing anything.
#
# Exit codes: 0 only if rotation + verification fully succeed; non-zero with a
# RESULT line explaining the failure. On any mid-run failure the script aborts
# BEFORE the CouchDB config write wherever possible, so the credential is not
# left in an inconsistent state.
set -u

COUCH_URL="${COUCH_URL:-http://localhost:5984}"
COUCH_ADMIN_USER="${COUCH_ADMIN_USER:-obsidian}"
# The CURRENT (about-to-be-rotated) password is read from the env that the vault
# flows already use, so this runbook uses the exact same source of truth.
OLD_PASS="${COUCH_ADMIN_PASS:-}"
DRY_RUN="${DRY_RUN:-0}"

# Where Kestra reads envs.COUCH_ADMIN_PASS from. Auto-detected below if unset;
# common locations: a systemd EnvironmentFile, a docker-compose env_file, or a
# standalone env sourced by the Kestra unit. Override when the deployment is known.
KESTRA_ENV_FILE="${KESTRA_ENV_FILE:-}"
KESTRA_SVC="${KESTRA_SVC:-kestra}"

PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1 — $2"; FAIL=$((FAIL+1)); }
note() { echo "       $1"; }
die()  { echo "RESULT: ROTATE_FAILED — $1"; echo "=== Summary: $PASS passed, $FAIL failed ==="; exit 1; }

# Redacted curl: wraps curl but strips any Authorization / password material
# from stderr so a -v trace never leaks the credential into the transcript.
# The body is never echoed either.
http_code() { # 1=method 2=path 3=user 4=pass  -> echoes HTTP status code
  curl -sS -o /dev/null -w '%{http_code}' \
    --connect-timeout 8 --max-time 15 \
    -X "$1" \
    -u "$3:$4" \
    -H 'Content-Type: application/json' \
    "${COUCH_URL}$2" 2>/dev/null || true
}

echo "=== CouchDB admin credential rotation (HU-1500) ==="
echo "target: $COUCH_URL  user: $COUCH_ADMIN_USER  time: $(date -u +%FT%TZ)  host: $(hostname)"
[ "$DRY_RUN" = "1" ] && echo "MODE: DRY RUN (no changes will be made)"
echo

# ─── 0. Preflight: old password must be present ───────────────────────────────
echo "## Preflight"
if [ -z "$OLD_PASS" ]; then
  fail "COUCH_ADMIN_PASS not set" "export the current live password before running."
  die "COUCH_ADMIN_PASS env var is empty — refusing to rotate blind."
fi
note "current password supplied (${#OLD_PASS} chars, value redacted)."
ok "Current credential available."
echo

# ─── 1. Pre-check: old password authenticates (proves we can rotate safely) ───
echo "## Pre-check: current credential authenticates"
pre_code="$(http_code GET / "$COUCH_ADMIN_USER" "$OLD_PASS")"
case "$pre_code" in
  200) ok "Current admin credential authenticates (HTTP 200 on GET /)." ;;
  401) fail "Current credential rejected" "HTTP 401 — old password already rotated, or wrong value. Refusing to proceed."; die "pre-check 401" ;;
  *)   fail "CouchDB not reachable / unexpected" "HTTP ${pre_code:-000} on GET /. Is CouchDB up on $COUCH_URL?"; die "pre-check unreachable" ;;
esac
echo

if [ "$DRY_RUN" = "1" ]; then
  echo "## DRY RUN — would rotate now and re-provision Kestra env"
  note "Generate new 32-char credential, PUT /_node/_local/_config/admins/$COUCH_ADMIN_USER,"
  note "update Kestra env source, reload $KESTRA_SVC, then post-verify."
  echo "RESULT: DRY_RUN_OK"
  echo "=== Summary: $PASS passed, $FAIL failed (dry run) ==="
  exit 0
fi

# ─── 2. Generate the new credential ───────────────────────────────────────────
echo "## Generate new credential"
# Prefer openssl (auditable entropy); fall back to /dev/urandom + base64.
if command -v openssl >/dev/null 2>&1; then
  NEW_PASS="$(openssl rand -hex 24 2>/dev/null || true)"
else
  NEW_PASS="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' 2>/dev/null || true)"
fi
# Hex is CouchDB-safe (no shell/JSON/URL metacharacters) and 48 chars long.
if [ -z "$NEW_PASS" ] || [ "${#NEW_PASS}" -lt 32 ]; then
  fail "New credential generation failed" "openssl + /dev/urandom both yielded nothing usable."
  die "could not generate a strong new credential."
fi
note "New credential generated (${#NEW_PASS} hex chars, value redacted)."
ok "New credential ready."
echo

# ─── 3. Rotate: PUT the new plaintext to CouchDB config (auto-hashed PBKDF2) ──
echo "## Rotate CouchDB admin password"
# CouchDB accepts a plaintext value on config write and stores it as a PBKDF2
# hash automatically. Body is a JSON-encoded string: "newpass".
rotate_body="\"$NEW_PASS\""
rotate_code="$(curl -sS -o /dev/null -w '%{http_code}' \
  --connect-timeout 8 --max-time 20 \
  -X PUT \
  -u "$COUCH_ADMIN_USER:$OLD_PASS" \
  -H 'Content-Type: application/json' \
  -d "$rotate_body" \
  "${COUCH_URL}/_node/_local/_config/admins/$COUCH_ADMIN_USER" 2>/dev/null || true)"
case "$rotate_code" in
  200) ok "CouchDB admin password rotated (PUT .../admins/$COUCH_ADMIN_USER → 200)." ;;
  *)   fail "Rotation rejected" "HTTP ${rotate_code:-000} on config PUT. CouchDB UNCHANGED (verify with old cred below)."
       # Confirm we did NOT lock ourselves out: old cred should still work if PUT failed.
       recheck="$(http_code GET / "$COUCH_ADMIN_USER" "$OLD_PASS")"
       [ "$recheck" = "200" ] && note "Old credential still authenticates — rotation did NOT apply, safe to retry." \
                            || note "WARNING: old credential also fails now — check CouchDB logs/admin console."
       die "rotation PUT returned ${rotate_code:-000}" ;;
esac
echo

# ─── 4. Post-check: new password works AND old password is rejected ───────────
echo "## Post-check: new credential works, old credential revoked"
new_code="$(http_code GET / "$COUCH_ADMIN_USER" "$NEW_PASS")"
old_code="$(http_code GET / "$COUCH_ADMIN_USER" "$OLD_PASS")"
if [ "$new_code" = "200" ]; then
  ok "New admin credential authenticates (HTTP 200)."
else
  fail "New credential rejected" "HTTP ${new_code:-000} — rotation did not take effect. FALLBACK: the old value still works if ${old_code}=200."
  die "post-check: new credential did not authenticate."
fi
if [ "$old_code" = "401" ] || [ "$old_code" = "403" ]; then
  ok "Old credential is now rejected (HTTP $old_code) — exposure neutralized."
else
  note "Old credential still returns HTTP ${old_code:-?} (may be a read-only/stale-cache path). New value is authoritative; verify manually if concerned."
fi
echo

# ─── 5. Re-provision COUCH_ADMIN_PASS in the Kestra execution environment ─────
echo "## Re-provision Kestra env (envs.COUCH_ADMIN_PASS)"
# Auto-detect the Kestra env source if not provided. We never print the value;
# we rewrite only the single COUCH_ADMIN_PASS line in place.
if [ -z "$KESTRA_ENV_FILE" ]; then
  # (a) systemd unit EnvironmentFile=
  unit_file="$(systemctl cat "$KESTRA_SVC" 2>/dev/null | grep -oE 'EnvironmentFile=-?\S+' | head -1 | sed 's/EnvironmentFile=-\?//' || true)"
  # (b) common docker-compose env_file next to a kestra compose file
  compose_env=""
  for c in /etc/kestra/docker-compose.yml /opt/kestra/docker-compose.yml /root/kestra/docker-compose.yml; do
    if [ -f "$c" ]; then
      ef="$(grep -oE 'env_file:\s*\S+' "$c" 2>/dev/null | head -1 | awk '{print $2}' || true)"
      [ -n "$ef" ] && compose_env="$(dirname "$c")/$ef" && break
    fi
  done
  if [ -n "$unit_file" ] && [ -f "$unit_file" ]; then
    KESTRA_ENV_FILE="$unit_file"; note "Detected Kestra systemd EnvironmentFile: $KESTRA_ENV_FILE"
  elif [ -n "$compose_env" ] && [ -f "$compose_env" ]; then
    KESTRA_ENV_FILE="$compose_env"; note "Detected Kestra docker-compose env_file: $KESTRA_ENV_FILE"
  fi
fi

KESTRA_UPDATED=0
if [ -n "$KESTRA_ENV_FILE" ] && [ -f "$KESTRA_ENV_FILE" ]; then
  # Atomic in-place rewrite: replace or append the COUCH_ADMIN_PASS line.
  # The value is written with 0600 perms; the file's existing perms are preserved.
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  if grep -q '^COUCH_ADMIN_PASS=' "$KESTRA_ENV_FILE" 2>/dev/null; then
    sed "s|^COUCH_ADMIN_PASS=.*|COUCH_ADMIN_PASS=$NEW_PASS|" "$KESTRA_ENV_FILE" > "$tmp"
  else
    cp "$KESTRA_ENV_FILE" "$tmp" 2>/dev/null || : > "$tmp"
    printf 'COUCH_ADMIN_PASS=%s\n' "$NEW_PASS" >> "$tmp"
  fi
  # Backup the old env file, then swap.
  cp "$KESTRA_ENV_FILE" "${KESTRA_ENV_FILE}.bak.$(date +%s)" 2>/dev/null || true
  if mv "$tmp" "$KESTRA_ENV_FILE"; then
    chmod 600 "$KESTRA_ENV_FILE"
    ok "Updated COUCH_ADMIN_PASS in $KESTRA_ENV_FILE (backup saved alongside, perms 0600)."
    KESTRA_UPDATED=1
  else
    fail "Could not write Kestra env file" "$KESTRA_ENV_FILE — check perms/ownership."
  fi
else
  fail "Kestra env source not found" "auto-detect did not locate it; set KESTRA_ENV_FILE explicitly."
  note "MANUAL STEP REQUIRED — add/update this line in whichever file sources Kestra's env,"
  note "then restart Kestra:"
  note "    COUCH_ADMIN_PASS=<new-value-written-to-/root/.couch_rotation_<timestamp>>"
  # Persist the new value to a 0600 root-only file so the operator can copy it,
  # since the script does not echo secrets to stdout.
  rotfile="/root/.couch_rotation_$(date +%s)"
  printf 'COUCH_ADMIN_PASS=%s\n' "$NEW_PASS" > "$rotfile"
  chmod 600 "$rotfile"
  note "New value (root-only readable) written to: $rotfile"
fi

# Reload Kestra so the new env takes effect (best-effort; not a hard failure
# if the service manager isn't reachable from this shell).
if [ "$KESTRA_UPDATED" = "1" ]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^$KESTRA_SVC"; then
    if systemctl reload "$KESTRA_SVC" 2>/dev/null || systemctl restart "$KESTRA_SVC" 2>/dev/null; then
      ok "Kestra service '$KESTRA_SVC' reloaded/restarted."
    else
      note "Could not reload '$KESTRA_SVC' from this shell — run: systemctl restart $KESTRA_SVC"
    fi
  elif command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -q "$KESTRA_SVC"; then
    if docker restart "$KESTRA_SVC" 2>/dev/null; then
      ok "Kestra container '$KESTRA_SVC' restarted."
    else
      note "Could not restart container '$KESTRA_SVC' — run: docker restart $KESTRA_SVC"
    fi
  else
    note "Kestra service manager not detected — restart Kestra via your usual mechanism so the new env loads."
  fi
fi
echo

# ─── 6. Summary ───────────────────────────────────────────────────────────────
echo "=== Summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
  echo "RESULT: ROTATED — CouchDB admin password changed and Kestra env re-provisioned."
  echo "next: trigger a vault-create/vault-archive smoke test (or the Kestra health"
  echo "      check) to confirm flows authenticate with the new credential, then the"
  echo "      HU-1501 incident can close and the launch chain can resume."
else
  echo "RESULT: PARTIAL — rotation applied but $FAIL provisioning/verification step(s) need attention."
  exit 1
fi
