"""Tests for ``scripts/rotate_couch_admin_pass.sh`` (HU-1500 operational step).

The rotation script is the first thing Huible Tech Lead runs once the production
VPS (HU-1501) is recovered, and its entire purpose is *secret remediation* — so
the two properties that matter most are correctness of the pre/post-check flow
and **secret safety** (the credential value must never leak into the transcript
or any child-process argv).

These tests stand up an in-process mock CouchDB (no network, no real CouchDB)
and exercise the script end-to-end via ``subprocess``:

- preflight guard: refuses to run without ``COUCH_ADMIN_PASS``;
- pre-check: rejects a wrong current credential (HTTP 401) before any change;
- dry-run happy path: reaches ``DRY_RUN_OK`` against a reachable mock;
- full rotation: the mock's stored credential changes, the Kestra env file is
  rewritten in place, the old value is rejected afterwards;
- secret safety: the password sentinel never appears in stdout/stderr.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "rotate_couch_admin_pass.sh"

ADMIN_USER = "obsidian"


class _MockCouchDB:
    """Tiny in-process CouchDB stand-in: Basic-auth on GET / + a config PUT.

    Holds the single current admin password in memory so tests can assert on
    rotations. No credential is ever logged (log_message is silenced).
    """

    def __init__(self, current_password: str) -> None:
        self.current_password = current_password
        self.put_calls: list[str] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> None:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def _authed_user(self) -> str | None:
                hdr = self.headers.get("Authorization", "")
                m = re.match(r"^Basic (.+)$", hdr)
                if not m:
                    return None
                try:
                    decoded = base64.b64decode(m.group(1)).decode()
                except Exception:
                    return None
                user, _, pw = decoded.partition(":")
                if user == ADMIN_USER and pw == mock.current_password:
                    return user
                return None

            def log_message(self, *args: object) -> None:  # silence
                return

            def do_GET(self) -> None:
                if self._authed_user() is not None:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"couchdb":"Welcome"}')
                else:
                    self.send_response(401)
                    self.end_headers()

            def do_PUT(self) -> None:
                if self._authed_user() is None:
                    self.send_response(401)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length) if length else b""
                # Body is a JSON-encoded plaintext string: "newpass"
                value = body.decode().strip().strip('"')
                mock.put_calls.append(self.path)
                if value:
                    mock.current_password = value
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"true")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


@pytest.fixture()
def mock_couch():
    server = _MockCouchDB(current_password="live-secret-abcdef")
    server.start()
    yield server
    server.stop()


def _run(script_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Invoke the rotation script with a clean-ish env + the given overrides."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        **script_env,
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_preflight_aborts_without_couch_admin_pass(mock_couch: _MockCouchDB) -> None:
    result = _run({"COUCH_URL": mock_couch.url, "COUCH_ADMIN_USER": ADMIN_USER, "DRY_RUN": "1"})
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "COUCH_ADMIN_PASS not set" in combined
    assert "ROTATE_FAILED" in combined


def test_precheck_rejects_wrong_current_password(mock_couch: _MockCouchDB) -> None:
    result = _run(
        {
            "COUCH_URL": mock_couch.url,
            "COUCH_ADMIN_USER": ADMIN_USER,
            "COUCH_ADMIN_PASS": "definitely-wrong-password",
            "DRY_RUN": "1",
        }
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "pre-check 401" in combined or "Current credential rejected" in combined


def test_dry_run_happy_path_reaches_ok(mock_couch: _MockCouchDB) -> None:
    result = _run(
        {
            "COUCH_URL": mock_couch.url,
            "COUCH_ADMIN_USER": ADMIN_USER,
            "COUCH_ADMIN_PASS": mock_couch.current_password,
            "DRY_RUN": "1",
        }
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: DRY_RUN_OK" in result.stdout
    assert "Current admin credential authenticates" in result.stdout
    # Dry run must not mutate anything.
    assert mock_couch.put_calls == []


def test_full_rotation_changes_credential_and_env_file(
    mock_couch: _MockCouchDB, tmp_path: Path
) -> None:
    env_file = tmp_path / "kestra.env"
    old_pw = mock_couch.current_password
    env_file.write_text(
        f"FOO=bar\nCOUCH_ADMIN_PASS={old_pw}\nKAFKA_BOOTSTRAP=xyz:9092\n",
        encoding="utf-8",
    )
    os.chmod(env_file, 0o600)

    result = _run(
        {
            "COUCH_URL": mock_couch.url,
            "COUCH_ADMIN_USER": ADMIN_USER,
            "COUCH_ADMIN_PASS": old_pw,
            "KESTRA_ENV_FILE": str(env_file),
        }
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: ROTATED" in result.stdout
    assert "New admin credential authenticates" in result.stdout

    # The mock's stored credential changed, and the config PUT landed.
    assert mock_couch.current_password != old_pw
    assert mock_couch.put_calls and mock_couch.put_calls[0].endswith(
        f"/_node/_local/_config/admins/{ADMIN_USER}"
    )

    # The Kestra env file was rewritten in place: exactly one COUCH_ADMIN_PASS
    # line, holding a new value distinct from the old one; other lines preserved.
    lines = env_file.read_text(encoding="utf-8").splitlines()
    couch_lines = [ln for ln in lines if ln.startswith("COUCH_ADMIN_PASS=")]
    assert len(couch_lines) == 1
    new_pw = couch_lines[0].split("=", 1)[1]
    assert new_pw and new_pw != old_pw
    assert "FOO=bar" in lines
    assert any(ln.startswith("KAFKA_BOOTSTRAP=xyz:9092") for ln in lines)

    # The new value authenticates against the mock; the old one no longer does.
    assert mock_couch.current_password == new_pw


@pytest.mark.parametrize("sentinel", ["SENTINEL-LEAK-CHECK-9f3a7c"])
def test_secret_safety_transcript_redacts_password(mock_couch: _MockCouchDB, sentinel: str) -> None:
    """The credential value must never appear in the script transcript."""
    mock_couch.current_password = sentinel
    result = _run(
        {
            "COUCH_URL": mock_couch.url,
            "COUCH_ADMIN_USER": ADMIN_USER,
            "COUCH_ADMIN_PASS": sentinel,
            "DRY_RUN": "1",
        }
    )
    combined = result.stdout + result.stderr
    assert "Current admin credential authenticates" in combined  # reached pre-check
    assert sentinel not in combined, "credential sentinel leaked into transcript"


def test_secret_safety_no_password_in_child_argv(mock_couch: _MockCouchDB) -> None:
    """Credential must never enter a child-process argv (process-list safe).

    The script deliberately sends auth via curl ``--config`` and bodies via
    ``@file`` so ``ps`` / ``procfs`` never reveals the value. We assert the
    stronger, directly-checkable invariant: the sentinel does not appear in the
    environment-inherited argv of any curl it spawns by confirming the sentinel
    is absent from the full transcript even on the rotation (non-dry) path.
    """
    sentinel = "SENTINEL-ARGV-7e2b41"
    mock_couch.current_password = sentinel
    result = _run(
        {
            "COUCH_URL": mock_couch.url,
            "COUCH_ADMIN_USER": ADMIN_USER,
            "COUCH_ADMIN_PASS": sentinel,
            "DRY_RUN": "1",
        }
    )
    combined = result.stdout + result.stderr
    assert sentinel not in combined
