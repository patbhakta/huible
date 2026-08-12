#!/usr/bin/env python3
"""
HUible Client Vault Provisioning — creates a complete client vault.

What this does (in order):
  1. Generates a unique client slug + credentials
  2. Creates a CouchDB database for Obsidian LiveSync
  3. Creates a CouchDB user with access scoped to that DB only
  4. Creates a local vault directory from the HUible template
  5. Git inits + pushes to GitHub as a private repo
  6. Registers the client in the tracking registry

Outputs JSON for Kestra to consume:
  {"client_slug": "...", "couch_db": "...", "couch_user": "...",
   "vault_path": "...", "git_url": "...", "livesync_uri": "..."}
"""

import argparse
import json
import os
import re
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────

COUCH_URL = "http://localhost:5984"
COUCH_ADMIN_USER = os.environ.get("COUCH_ADMIN_USER", "obsidian")
COUCH_ADMIN_PASS = os.environ.get("COUCH_ADMIN_PASS", "***REMOVED***")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "patbhakta")  # user or org

VAULT_TEMPLATE_DIR = Path(os.environ.get(
    "VAULT_TEMPLATE_DIR", "/root/repos/huible/scripts/vault-template"
))
CLIENT_VAULTS_BASE = Path(os.environ.get(
    "CLIENT_VAULTS_BASE", "/root/repos/client-vaults"
))
REGISTRY_FILE = Path(os.environ.get(
    "VAULT_REGISTRY", "/root/repos/client-vaults/registry.json"
))

LIVESYNC_BASE_URL = os.environ.get(
    "LIVESYNC_BASE_URL", "https://brain.bhakta.us"
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def couch_request(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated CouchDB request."""
    url = f"{COUCH_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    import base64
    cred = base64.b64encode(f"{COUCH_ADMIN_USER}:{COUCH_ADMIN_PASS}".encode()).decode()
    req.add_header("Authorization", f"Basic {cred}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}", "body": body_text}
    except Exception as e:
        return {"error": str(e)}


def gen_password(length: int = 24) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def slugify(name: str) -> str:
    """Convert a display name to a filesystem-safe slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "client"


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, raise on failure if check=True."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=60)
    if check and result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}", file=sys.stderr)
        print(f"stderr: {result.stderr}", file=sys.stderr)
        raise RuntimeError(f"Command failed: {cmd[0]}")
    return result


def github_api(method: str, endpoint: str, body: dict | None = None) -> dict:
    """Call GitHub REST API."""
    url = f"https://api.github.com{endpoint}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}", "body": body_text}
    except Exception as e:
        return {"error": str(e)}


# ─── Core Steps ───────────────────────────────────────────────────────────────

def create_couchdb_database(db_name: str) -> dict:
    """Create a CouchDB database for client LiveSync."""
    print(f"[couchdb] Creating database: {db_name}")
    result = couch_request("PUT", f"/{db_name}")
    if "error" in result and "file_exists" not in result.get("body", ""):
        # If it already exists, that's fine for idempotency
        if "HTTP 412" not in result.get("error", ""):
            print(f"[couchdb] Warning: {result}")
    print(f"[couchdb] Database ready: {db_name}")
    return result


def create_couchdb_user(db_name: str, username: str, password: str) -> dict:
    """Create a CouchDB user scoped to a single database via security doc."""
    print(f"[couchdb] Creating user: {username} for db: {db_name}")

    # Create user document in _users
    user_doc = {
        "_id": f"org.couchdb.user:{username}",
        "name": username,
        "roles": [f"{db_name}-user"],
        "type": "user",
        "password": password,
    }
    result = couch_request("PUT", f"/_users/org.couchdb.user:{username}", user_doc)
    if "error" in result and "HTTP 409" not in result.get("error", ""):
        print(f"[couchdb] User creation note: {result}")

    # Set security on the database — only this user's role can access
    security = {
        "admins": {"names": [COUCH_ADMIN_USER], "roles": []},
        "members": {"names": [username], "roles": [f"{db_name}-user"]},
    }
    sec_result = couch_request("PUT", f"/{db_name}/_security", security)
    print(f"[couchdb] Security set for {db_name}")
    return sec_result


def create_vault_from_template(client_slug: str, persona_name: str,
                                relationship: str, client_name: str) -> Path:
    """Create a vault directory from the HUible template."""
    vault_path = CLIENT_VAULTS_BASE / client_slug
    print(f"[vault] Creating vault at: {vault_path}")

    if vault_path.exists():
        print(f"[vault] Directory exists, using existing: {vault_path}")
    else:
        vault_path.mkdir(parents=True, exist_ok=True)

    # Copy template if it exists
    if VAULT_TEMPLATE_DIR.exists():
        run(["cp", "-rn", str(VAULT_TEMPLATE_DIR) + "/.", str(vault_path) + "/"],
            check=False)

    # Always ensure minimum required structure
    for subdir in ["", ".obsidian", "persona", "memories", "dialogues", ".private"]:
        d = vault_path / subdir
        d.mkdir(parents=True, exist_ok=True)

    # Write client identity file
    identity = {
        "client_slug": client_slug,
        "client_name": client_name,
        "persona_name": persona_name,
        "relationship": relationship,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "active",
    }
    (vault_path / ".private" / "client-identity.json").write_text(
        json.dumps(identity, indent=2)
    )

    # Write persona profile placeholder (OKF frontmatter)
    persona_md = f"""---
okf_version: "0.2"
doc_type: persona-profile
client_slug: "{client_slug}"
persona_name: "{persona_name}"
relationship: "{relationship}"
created: {datetime.now(UTC).strftime("%Y-%m-%d")}
---

# {persona_name}

> {relationship} of {client_name}

## Identity

*(To be filled during onboarding conversations)*

## Voice & Communication Style

*(To be filled during onboarding conversations)*

## Values & Beliefs

*(To be filled during onboarding conversations)*

## Memories

*(To be filled during onboarding conversations)*
"""
    (vault_path / "persona" / "persona-profile.md").write_text(persona_md)

    # Write .gitignore for sensitive data
    gitignore = """# Private client data — never commit
.private/client-identity.json
.obsidian/workspace*
.obsidian/app.json
"""
    (vault_path / ".gitignore").write_text(gitignore)

    print(f"[vault] Template written to {vault_path}")
    return vault_path


def init_git_and_push(vault_path: Path, client_slug: str) -> str:
    """Initialize git repo and push to GitHub as a private repo."""
    print(f"[git] Initializing repo for {client_slug}")

    repo_name = f"huible-client-{client_slug}"

    # Init local git
    run(["git", "init"], cwd=str(vault_path), check=False)
    run(["git", "add", "-A"], cwd=str(vault_path))
    run(["git", "commit", "-m", f"feat: initialize client vault for {client_slug}"],
        cwd=str(vault_path), check=False)

    # Create GitHub repo
    gh_result = github_api("POST", f"/user/repos", {
        "name": repo_name,
        "private": True,
        "description": f"HUible client vault — {client_slug}",
        "auto_init": False,
    })

    if "error" in gh_result:
        # Repo may already exist — try to use it
        print(f"[git] GitHub repo creation note: {gh_result.get('error')}")

    git_url = f"https://{GITHUB_ORG}:{GITHUB_TOKEN}@github.com/{GITHUB_ORG}/{repo_name}.git"

    # Add remote and push
    run(["git", "remote", "remove", "origin"], cwd=str(vault_path), check=False)
    run(["git", "remote", "add", "origin", git_url], cwd=str(vault_path))
    push_result = run(["git", "push", "-u", "origin", "main"], cwd=str(vault_path), check=False)

    if push_result.returncode != 0:
        # Try master branch
        run(["git", "branch", "-M", "main"], cwd=str(vault_path), check=False)
        run(["git", "push", "-u", "origin", "main"], cwd=str(vault_path), check=False)

    # Return clean URL (no token)
    clean_url = f"https://github.com/{GITHUB_ORG}/{repo_name}"
    print(f"[git] Pushed to {clean_url}")
    return clean_url


def register_client(client_slug: str, client_name: str, persona_name: str,
                     relationship: str, couch_db: str, couch_user: str,
                     couch_pass: str, vault_path: str, git_url: str,
                     livesync_uri: str) -> dict:
    """Register the client in the tracking registry."""
    print(f"[registry] Registering {client_slug}")

    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if REGISTRY_FILE.exists():
        registry = json.loads(REGISTRY_FILE.read_text())
    else:
        registry = {"clients": []}

    entry = {
        "client_slug": client_slug,
        "client_name": client_name,
        "persona_name": persona_name,
        "relationship": relationship,
        "couch_db": couch_db,
        "couch_user": couch_user,
        "couch_password": couch_pass,  # encrypted at rest by file perms
        "vault_path": vault_path,
        "git_url": git_url,
        "livesync_uri": livesync_uri,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "active",
    }

    # Remove any existing entry with same slug (idempotent)
    registry["clients"] = [
        c for c in registry["clients"] if c.get("client_slug") != client_slug
    ]
    registry["clients"].append(entry)

    REGISTRY_FILE.write_text(json.dumps(registry, indent=2))
    os.chmod(REGISTRY_FILE, 0o600)

    print(f"[registry] Registered in {REGISTRY_FILE}")
    return entry


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Provision a HUible client vault")
    parser.add_argument("--client-name", required=True, help="Display name of the client")
    parser.add_argument("--persona-name", required=True, help="Name of the deceased persona")
    parser.add_argument("--relationship", required=True, help="Client's relationship to persona (e.g. 'wife')")
    args = parser.parse_args()

    # Generate identifiers
    timestamp = datetime.now().strftime("%Y%m%d")
    base_slug = slugify(args.persona_name)
    client_slug = f"{base_slug}-{timestamp}"
    couch_db = f"client-{client_slug}"
    couch_user = f"user-{client_slug}"[:50]  # CouchDB user max length
    couch_pass = gen_password()

    print(f"\n{'='*60}")
    print(f"  Provisioning client vault: {client_slug}")
    print(f"  Persona: {args.persona_name} ({args.relationship} of {args.client_name})")
    print(f"{'='*60}\n")

    # Step 1: Create CouchDB database
    create_couchdb_database(couch_db)

    # Step 2: Create scoped CouchDB user
    create_couchdb_user(couch_db, couch_user, couch_pass)

    # Step 3: Create vault from template
    vault_path = create_vault_from_template(
        client_slug, args.persona_name, args.relationship, args.client_name
    )

    # Step 4: Git init + push (if token available)
    git_url = ""
    if GITHUB_TOKEN:
        git_url = init_git_and_push(vault_path, client_slug)
    else:
        print("[git] No GITHUB_TOKEN — skipping remote push")

    # Step 5: Build LiveSync URI for the client
    livesync_uri = f"{LIVESYNC_BASE_URL}/{couch_db}"
    # The client uses this in Obsidian LiveSync plugin settings:
    #   URI: https://brain.bhakta.us
    #   DB: client-<slug>
    #   Username: user-<slug>
    #   Password: <generated>

    # Step 6: Register in tracking
    entry = register_client(
        client_slug, args.client_name, args.persona_name, args.relationship,
        couch_db, couch_user, couch_pass, str(vault_path), git_url, livesync_uri
    )

    # Output for Kestra
    output = {
        "client_slug": client_slug,
        "couch_db": couch_db,
        "couch_user": couch_user,
        "vault_path": str(vault_path),
        "git_url": git_url,
        "livesync_uri": livesync_uri,
        "livesync_db": couch_db,
        "status": "created",
    }

    print(f"\n{'='*60}")
    print(f"  ✅ Vault provisioned: {client_slug}")
    print(f"  CouchDB: {couch_db}")
    print(f"  User: {couch_user}")
    print(f"  Vault: {vault_path}")
    print(f"  Git: {git_url or '(no remote)'}")
    print(f"  LiveSync: {LIVESYNC_BASE_URL} (db: {couch_db})")
    print(f"{'='*60}\n")

    # Kestra output protocol
    print("::" + json.dumps({"outputs": output}) + "::")


if __name__ == "__main__":
    main()
