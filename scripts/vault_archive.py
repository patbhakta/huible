#!/usr/bin/env python3
"""
HUible Client Vault Archival — safely deprovisions and archives a client vault.

What this does (in order):
  1. Exports CouchDB database to a backup file
  2. Marks the GitHub repo as archived (or deletes if --purge)
  3. Deletes the CouchDB database + user
  4. Archives local vault directory (tar.gz)
  5. Updates the registry entry to "archived" status

Outputs JSON for Kestra:
  {"client_slug": "...", "backup_path": "...", "archive_path": "...", "status": "archived"}
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# ─── Config ───────────────────────────────────────────────────────────────────

COUCH_URL = "http://localhost:5984"
COUCH_ADMIN_USER = os.environ.get("COUCH_ADMIN_USER", "obsidian")
COUCH_ADMIN_PASS = os.environ.get("COUCH_ADMIN_PASS", "")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "patbhakta")

CLIENT_VAULTS_BASE = Path(os.environ.get(
    "CLIENT_VAULTS_BASE", "/root/repos/client-vaults"
))
ARCHIVE_DIR = Path(os.environ.get(
    "VAULT_ARCHIVE_DIR", "/root/repos/client-vaults/archived"
))
REGISTRY_FILE = Path(os.environ.get(
    "VAULT_REGISTRY", "/root/repos/client-vaults/registry.json"
))


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
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        return {"error": f"HTTP {e.code}", "body": body_text}
    except Exception as e:
        return {"error": str(e)}


def run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, raise on failure if check=True."""
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=120)
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


def load_registry() -> dict:
    """Load the client registry."""
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text())
    return {"clients": []}


def save_registry(registry: dict):
    """Save the client registry."""
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2))
    os.chmod(REGISTRY_FILE, 0o600)


def find_client(client_slug: str) -> dict | None:
    """Find a client entry in the registry."""
    registry = load_registry()
    for c in registry.get("clients", []):
        if c.get("client_slug") == client_slug:
            return c
    return None


# ─── Core Steps ───────────────────────────────────────────────────────────────

def backup_couchdb(db_name: str, backup_dir: Path) -> Path:
    """Export CouchDB database to a JSON backup file."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_file = backup_dir / f"{db_name}-{timestamp}.json"

    print(f"[couchdb] Backing up {db_name} → {backup_file}")

    # Use _all_docs with include_docs=true for a full export
    result = couch_request("GET", f"/{db_name}/_all_docs?include_docs=true")

    if "error" in result:
        print(f"[couchdb] Backup WARNING: {result}")
        # Write what we have
        backup_file.write_text(json.dumps(result, indent=2))
    else:
        backup_file.write_text(json.dumps(result, indent=2))
        doc_count = len(result.get("rows", []))
        print(f"[couchdb] Backed up {doc_count} docs to {backup_file}")

    return backup_file


def archive_github_repo(client_slug: str, purge: bool = False) -> str:
    """Archive or delete the GitHub repo for a client."""
    repo_name = f"huible-client-{client_slug}"

    if not GITHUB_TOKEN:
        return "(no token — skipped)"

    if purge:
        print(f"[git] Deleting GitHub repo: {repo_name}")
        result = github_api("DELETE", f"/repos/{GITHUB_ORG}/{repo_name}")
        return f"deleted: {repo_name}"
    else:
        print(f"[git] Archiving GitHub repo: {repo_name}")
        result = github_api("PATCH", f"/repos/{GITHUB_ORG}/{repo_name}", {
            "archived": True,
            "description": f"[ARCHIVED] HUible client vault — {client_slug}"
        })
        if "error" in result:
            print(f"[git] Archive note: {result.get('error')}")
            return f"archive-attempted: {repo_name}"
        return f"archived: {repo_name}"


def delete_couchdb_database(db_name: str):
    """Delete the CouchDB database."""
    print(f"[couchdb] Deleting database: {db_name}")
    result = couch_request("DELETE", f"/{db_name}")
    if "error" in result:
        print(f"[couchdb] Delete note: {result}")
    else:
        print(f"[couchdb] Deleted: {db_name}")


def delete_couchdb_user(username: str):
    """Delete the CouchDB user."""
    print(f"[couchdb] Deleting user: {username}")

    # First get the user doc to find _rev
    result = couch_request("GET", f"/_users/org.couchdb.user:{username}")
    if "error" in result:
        print(f"[couchdb] User not found (may already be deleted): {result}")
        return

    rev = result.get("_rev")
    if rev:
        del_result = couch_request(
            "DELETE",
            f"/_users/org.couchdb.user:{username}?rev={rev}"
        )
        if "error" in del_result:
            print(f"[couchdb] User delete note: {del_result}")
        else:
            print(f"[couchdb] User deleted: {username}")


def archive_local_vault(vault_path: str, client_slug: str) -> Path:
    """Archive the local vault directory to a tar.gz."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_file = ARCHIVE_DIR / f"{client_slug}-{timestamp}.tar.gz"

    source = Path(vault_path)
    if not source.exists():
        print(f"[vault] Source path does not exist: {vault_path}")
        return archive_file

    print(f"[vault] Archiving {vault_path} → {archive_file}")

    # Create tar.gz excluding .git
    run([
        "tar", "czf", str(archive_file),
        "--exclude=.git",
        "-C", str(source.parent),
        source.name
    ], check=False)

    # Remove the original directory
    if archive_file.exists():
        shutil.rmtree(str(source), ignore_errors=True)
        print(f"[vault] Original directory removed: {vault_path}")

    return archive_file


def update_registry_status(client_slug: str, backup_path: str, archive_path: str):
    """Update the registry entry to 'archived' status."""
    registry = load_registry()

    for c in registry.get("clients", []):
        if c.get("client_slug") == client_slug:
            c["status"] = "archived"
            c["archived_at"] = datetime.now(UTC).isoformat()
            c["backup_path"] = backup_path
            c["archive_path"] = archive_path
            break

    save_registry(registry)
    print(f"[registry] Updated {client_slug} → archived")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Archive a HUible client vault")
    parser.add_argument("--client-slug", required=True, help="Client slug from provisioning")
    parser.add_argument("--purge", action="store_true",
                        help="Permanently delete GitHub repo instead of archiving")
    parser.add_argument("--keep-data", action="store_true",
                        help="Skip CouchDB/local deletion (backup only)")
    args = parser.parse_args()

    client = find_client(args.client_slug)
    if not client:
        print(f"ERROR: Client '{args.client_slug}' not found in registry", file=sys.stderr)
        print("Available clients:")
        registry = load_registry()
        for c in registry.get("clients", []):
            print(f"  - {c.get('client_slug')} ({c.get('status', '?')})")
        sys.exit(1)

    if client.get("status") == "archived":
        print(f"Client '{args.client_slug}' is already archived")
        sys.exit(0)

    couch_db = client["couch_db"]
    couch_user = client["couch_user"]
    vault_path = client["vault_path"]

    print(f"\n{'='*60}")
    print(f"  Archiving client vault: {args.client_slug}")
    print(f"  CouchDB: {couch_db}")
    print(f"  User: {couch_user}")
    print(f"  Vault: {vault_path}")
    print(f"{'='*60}\n")

    # Step 1: Backup CouchDB
    backup_dir = ARCHIVE_DIR / args.client_slug / "couchdb"
    backup_path = backup_couchdb(couch_db, backup_dir)

    # Step 2: Archive GitHub repo
    gh_status = archive_github_repo(args.client_slug, purge=args.purge)
    print(f"[git] {gh_status}")

    if not args.keep_data:
        # Step 3: Delete CouchDB database
        delete_couchdb_database(couch_db)

        # Step 4: Delete CouchDB user
        delete_couchdb_user(couch_user)

        # Step 5: Archive local vault
        archive_path = archive_local_vault(vault_path, args.client_slug)
    else:
        archive_path = "(kept — backup only)"
        print("[vault] --keep-data: skipping deletion")

    # Step 6: Update registry
    update_registry_status(args.client_slug, str(backup_path), str(archive_path))

    # Output for Kestra
    output = {
        "client_slug": args.client_slug,
        "backup_path": str(backup_path),
        "archive_path": str(archive_path),
        "github_status": gh_status,
        "status": "archived",
    }

    print(f"\n{'='*60}")
    print(f"  ✅ Vault archived: {args.client_slug}")
    print(f"  Backup: {backup_path}")
    print(f"  Archive: {archive_path}")
    print(f"  GitHub: {gh_status}")
    print(f"{'='*60}\n")

    # Kestra output protocol
    print("::" + json.dumps({"outputs": output}) + "::")


if __name__ == "__main__":
    main()
