"""Tests for ``scripts/verify_vps_recovery.sh`` (HU-1501 closure gate).

This is the FIRST step of the HU-1501 recovery trio — it proves the production
VPS is actually back (ICMP + SSH + Kestra + CouchDB + Tailscale) *before* the
HU-1500 credential rotation runs or any flow is re-enabled. A false "recovered"
here would point the rotation runbook at a half-up box and silently corrupt the
recovery, so the properties that matter most are:

- **gate strictness**: a single hard FAIL → ``VPS_NOT_READY`` / exit 1, never
  "proceed with HU-1500";
- **soft-vs-hard classification**: CouchDB being localhost-bound on the tailnet
  IP is a *note*, not a failure (the rotation runs on-box); a missing/offline
  Tailscale node *is* a failure;
- **TCP-open ≠ HTTP-alive**: Kestra's port accepting a connection is not enough
  — the HTTP probe must return a real status code;
- **graceful degradation**: on a host without the ``tailscale`` CLI the verifier
  still runs and emits a note instead of crashing.

The script has no test harness of its own and talks to the network via
``ping``/``curl``/``tailscale``, so these tests shadow those three commands with
fixture stubs driven by scenario env vars (no real network, no real hosts). The
production target IPs are intentionally left at their defaults so the tests also
lock in the prod targets documented in the script header.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_vps_recovery.sh"

# curl-stub env keys, one per ``host:port`` the verifier probes (prod defaults).
SSH22 = "CURL_208_84_102_243_22"
KESTRA_PUB = "CURL_208_84_102_243_8080"
KESTRA_TS = "CURL_100_75_34_75_8080"
COUCH_TS = "CURL_100_109_142_4_5984"

_TS_ONLINE = (
    "100.101.235.117 ip-208-84-102-245 root@ -\n"
    "100.109.142.4   ip-208-84-102-243  root@ -\n"
    "100.75.34.75    kestra-on-vps      root@ -\n"
)
_TS_OFFLINE = (
    "100.101.235.117 ip-208-84-102-245 root@ -\n"
    "100.109.142.4   ip-208-84-102-243  root@ offline; last seen 2d ago\n"
    "100.75.34.75    kestra-on-vps      root@ offline; last seen 2d ago\n"
)

# ── Stub binaries ────────────────────────────────────────────────────────────
# Each stub is driven by scenario env vars so a single fixture can emulate every
# reachability combination without touching the network.

PING_STUB = """\
#!/usr/bin/env bash
# emulate `ping -c 2 -W 3 HOST` → 0 reachable / 1 unreachable.
exit "${PING_RESULT:-0}"
"""

CURL_STUB = """\
#!/usr/bin/env bash
# emulate curl against http://HOST:PORT/, driven by CURL_<host>_<port> env.
# value: ok | refuse | timeout | dns | http:CODE | body:TEXT
# The URL is the final positional argument in every call site of the verifier.
last=""
for a in "$@"; do last="$a"; done
rest="${last#http://}"
hostport="${rest%%/*}"
host="${hostport%%:*}"
port="${hostport##*:}"
hkey="$(printf '%s' "$host" | tr '.' '_')"
key="CURL_${hkey}_${port}"
val="${!key:-refuse}"

want_write=0
to_devnull=0
prev=""
for a in "$@"; do
  [ "$a" = "-w" ] && want_write=1
  if [ "$prev" = "-o" ] && [ "$a" = "/dev/null" ]; then to_devnull=1; fi
  prev="$a"
done

code="000"; body=""
case "$val" in
  ok)        code="200" ;;
  refuse)    exit 7 ;;
  timeout)   exit 28 ;;
  dns)       exit 6 ;;
  http:*)    code="${val#http:}" ;;
  body:*)    code="200"; body="${val#body:}" ;;
  *)         exit 7 ;;
esac

[ "$want_write" = 1 ] && printf '%s' "$code"
if [ "$to_devnull" = 0 ] && [ -n "$body" ]; then printf '%s' "$body"; fi
case "$code" in 4*|5*) exit 22 ;; esac
exit 0
"""

TAILSCALE_STUB = """\
#!/usr/bin/env bash
# emulate `tailscale status` — prints the TS_STATUS_OUT fixture verbatim.
printf '%s\n' "${TS_STATUS_OUT:-}"
exit 0
"""


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    """Stub dir (first on PATH) with ping/curl/tailscale shadows."""
    d = tmp_path / "bin"
    d.mkdir()
    _write(d / "ping", PING_STUB)
    _write(d / "curl", CURL_STUB)
    _write(d / "tailscale", TAILSCALE_STUB)
    return d


def _run(bin_dir: Path, scenario: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/tmp"),
        **scenario,
    }
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _green() -> dict[str, str]:
    """Every probe healthy → the only scenario that may yield VPS_RECOVERED."""
    return {
        "PING_RESULT": "0",
        SSH22: "ok",
        KESTRA_PUB: "http:200",
        KESTRA_TS: "ok",
        COUCH_TS: 'body:{"couchdb":"Welcome","version":"3.3.2"}',
        "TS_STATUS_OUT": _TS_ONLINE,
    }


# ── Happy path ───────────────────────────────────────────────────────────────


def test_all_green_yields_vps_recovered(bin_dir: Path) -> None:
    result = _run(bin_dir, _green())
    assert result.returncode == 0, result.stdout
    assert "RESULT: VPS_RECOVERED" in result.stdout
    assert "proceed with HU-1500" in result.stdout
    assert "0 failed" in result.stdout
    # Each hard-gate section reported a PASS.
    assert "ICMP replies" in result.stdout
    assert "SSH :22 open" in result.stdout
    assert "Kestra HTTP responds" in result.stdout
    assert "is online" in result.stdout  # both tailscale nodes


# ── Gate strictness: any hard FAIL → NOT_READY ──────────────────────────────


def test_host_still_down_reports_not_ready(bin_dir: Path) -> None:
    scenario = _green()
    scenario.update(
        {
            "PING_RESULT": "1",
            SSH22: "refuse",
            KESTRA_PUB: "timeout",
            KESTRA_TS: "refuse",
            COUCH_TS: "timeout",
            "TS_STATUS_OUT": _TS_OFFLINE,
        }
    )
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "RESULT: VPS_NOT_READY" in result.stdout
    assert "do not proceed" in result.stdout
    assert "ICMP unreachable" in result.stdout
    assert "SSH :22 closed" in result.stdout
    assert "Kestra :8080 closed" in result.stdout


def test_kestra_down_but_host_up_blocks_recovery(bin_dir: Path) -> None:
    """Host booted but Kestra did not return — recovery must NOT proceed."""
    scenario = _green()
    scenario[KESTRA_PUB] = "refuse"
    scenario[KESTRA_TS] = "refuse"
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "Kestra :8080 closed" in result.stdout
    assert "RESULT: VPS_NOT_READY" in result.stdout
    # The host itself is reachable, so this is a Kestra-only outage.
    assert "ICMP replies" in result.stdout
    assert "SSH :22 open" in result.stdout


def test_kestra_port_open_but_http_dead_fails(bin_dir: Path) -> None:
    """TCP accepts but the HTTP daemon returns nothing — port-open is not enough."""
    scenario = _green()
    scenario[KESTRA_PUB] = "http:000"
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "Kestra :8080 open on" in result.stdout  # port probe passed
    assert "Kestra HTTP not responding" in result.stdout
    assert "RESULT: VPS_NOT_READY" in result.stdout


def test_tailscale_node_offline_fails(bin_dir: Path) -> None:
    scenario = _green()
    scenario["TS_STATUS_OUT"] = _TS_OFFLINE
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "offline" in result.stdout
    assert "RESULT: VPS_NOT_READY" in result.stdout


def test_tailscale_node_missing_fails(bin_dir: Path) -> None:
    scenario = _green()
    scenario["TS_STATUS_OUT"] = (
        "100.101.235.117 ip-208-84-102-245 root@ -\n"
        "100.109.142.4   ip-208-84-102-243  root@ -\n"
    )  # kestra-on-vps line omitted entirely
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "missing" in result.stdout
    assert "kestra-on-vps" in result.stdout
    assert "RESULT: VPS_NOT_READY" in result.stdout


# ── Soft-vs-hard classification ──────────────────────────────────────────────


def test_couchdb_localhost_bound_is_soft_note_not_failure(bin_dir: Path) -> None:
    """CouchDB is typically localhost-bound on the VPS — that must NOT block."""
    scenario = _green()
    scenario[COUCH_TS] = "refuse"
    result = _run(bin_dir, scenario)
    assert result.returncode == 0, result.stdout
    assert "RESULT: VPS_RECOVERED" in result.stdout
    assert "localhost-bound" in result.stdout
    assert "rotation must run on-box" in result.stdout
    # The closed CouchDB port is a note, not a counted failure.
    assert "0 failed" in result.stdout


# ── Graceful degradation without the tailscale CLI ──────────────────────────


def test_tailscale_cli_absent_degrades_gracefully(tmp_path: Path) -> None:
    """A recovery host without the tailscale CLI still verifies and notes it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write(bin_dir / "ping", PING_STUB)
    _write(bin_dir / "curl", CURL_STUB)
    # NOTE: no `tailscale` stub on PATH.
    # Curate a minimal system bin (date/hostname/grep/python3/sed) that excludes
    # the real tailscale, so `command -v tailscale` genuinely fails.
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    for name in ("bash", "sh", "date", "hostname", "grep", "python3", "sed", "tr"):
        src = shutil.which(name)
        if src:
            (sysbin / name).symlink_to(src)
    env = {
        "PATH": f"{bin_dir}:{sysbin}",
        "HOME": os.environ.get("HOME", "/tmp"),
        **_green(),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout
    assert "tailscale CLI not available" in result.stdout
    assert "RESULT: VPS_RECOVERED" in result.stdout
