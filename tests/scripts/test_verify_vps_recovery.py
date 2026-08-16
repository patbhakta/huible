"""Tests for ``scripts/verify_vps_recovery.sh`` (prod posture verifier).

Guards the canonical-addresses contract that twice prevented-worth of false
incidents depends on (HU-1777, HU-1823):

- **defaults target CURRENT prod** (.245 since the HU-1715 cutover) — never the
  decommissioned .243;
- **legacy refusal**: probing .243 without ``PROBE_LEGACY_243=1`` exits 2 with
  guidance, not a scary ``VPS_NOT_READY``;
- **retired stack**: Kestra/CouchDB checks are skipped (note, not failure) in
  default mode since HU-1706/HU-1681 retired the LiveSync stack, and the edge
  ``:80 → 308`` Caddy health pin (HU-1672) is checked instead;
- **gate strictness**: a single hard FAIL → ``VPS_NOT_READY`` / exit 1;
- **legacy opt-in**: with ``PROBE_LEGACY_243=1`` the old .243 checks
  (Kestra/CouchDB included) still work for archaeology/power-cycle
  verification of the old box;
- **graceful degradation**: on a host without the ``tailscale`` CLI the verifier
  still runs and emits a note instead of crashing.

The script talks to the network via ``ping``/``curl``/``tailscale``, so these
tests shadow those three commands with fixture stubs driven by scenario env
vars (no real network, no real hosts). The production target IPs are
intentionally left at their defaults so the tests also lock in the prod targets
documented in the script header.
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
SSH22 = "CURL_208_84_102_245_22"
EDGE80 = "CURL_208_84_102_245_80"
# Legacy (.243) keys used only in PROBE_LEGACY_243 mode.
LEGACY_SSH22 = "CURL_208_84_102_243_22"
LEGACY_KESTRA_PUB = "CURL_208_84_102_243_8080"
LEGACY_COUCH_TS = "CURL_100_109_142_4_5984"

_TS_ONLINE = (
    "100.101.235.117 ip-208-84-102-245 root@ -\n"
    "100.75.34.75    kestra-on-vps      root@ -\n"
)
_TS_OFFLINE = (
    "100.101.235.117 ip-208-84-102-245 root@ offline; last seen 2d ago\n"
    "100.75.34.75    kestra-on-vps      root@ -\n"
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
# curl defaults to :80 when the URL carries no explicit port.
if [ "$port" = "$hostport" ]; then port=80; fi
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
printf '%s\\n' "${TS_STATUS_OUT:-}"
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
    """Every default-mode probe healthy → the only scenario that may yield VPS_RECOVERED."""
    return {
        "PING_RESULT": "0",
        SSH22: "ok",
        EDGE80: "http:308",
        "TS_STATUS_OUT": _TS_ONLINE,
    }


# ── Canonical-addresses guard ────────────────────────────────────────────────


def test_defaults_target_current_prod_not_legacy_243(bin_dir: Path) -> None:
    """Bare run must probe .245 (current prod) — never the decommissioned .243."""
    result = _run(bin_dir, _green())
    assert result.returncode == 0, result.stdout
    assert "target: 208.84.102.245" in result.stdout
    assert "208.84.102.243" not in result.stdout.replace("208.84.102.243 is DECOMMISSIONED", "")


def test_legacy_243_refused_without_opt_in(bin_dir: Path) -> None:
    """Probing .243 by explicit override must exit 2 with guidance, not VPS_NOT_READY."""
    result = _run(bin_dir, {**_green(), "VPS_PUBLIC": "208.84.102.243"})
    assert result.returncode == 2
    assert "DECOMMISSIONED" in result.stderr
    assert "PROBE_LEGACY_243=1" in result.stderr
    assert "HU-1823" in result.stderr
    assert "VPS_NOT_READY" not in result.stdout


# ── Happy path ───────────────────────────────────────────────────────────────


def test_all_green_yields_vps_recovered(bin_dir: Path) -> None:
    result = _run(bin_dir, _green())
    assert result.returncode == 0, result.stdout
    assert "RESULT: VPS_RECOVERED" in result.stdout
    assert "0 failed" in result.stdout
    # Each hard-gate section reported a PASS.
    assert "ICMP replies" in result.stdout
    assert "SSH :22 open" in result.stdout
    assert "308" in result.stdout  # edge health pin
    assert "is online" in result.stdout  # tailscale node(s)


def test_retired_stack_skipped_by_default_with_note(bin_dir: Path) -> None:
    """Kestra/CouchDB are retired (HU-1706/HU-1681) — skipped, not failed."""
    result = _run(bin_dir, _green())
    assert result.returncode == 0, result.stdout
    assert "retired with the LiveSync stack" in result.stdout
    assert "Kestra HTTP not responding" not in result.stdout
    assert "0 failed" in result.stdout


# ── Gate strictness: any hard FAIL → NOT_READY ──────────────────────────────


def test_host_still_down_reports_not_ready(bin_dir: Path) -> None:
    scenario = _green()
    scenario.update(
        {
            "PING_RESULT": "1",
            SSH22: "refuse",
            EDGE80: "timeout",
            "TS_STATUS_OUT": _TS_OFFLINE,
        }
    )
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "RESULT: VPS_NOT_READY" in result.stdout
    assert "ICMP unreachable" in result.stdout
    assert "SSH :22 closed" in result.stdout


def test_edge_not_308_fails_even_if_ports_up(bin_dir: Path) -> None:
    """Caddy answering :80 with anything but the 308 pin is an edge failure."""
    scenario = _green()
    scenario[EDGE80] = "http:200"
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "Edge :80 unexpected code" in result.stdout
    assert "RESULT: VPS_NOT_READY" in result.stdout


def test_edge_dead_fails(bin_dir: Path) -> None:
    scenario = _green()
    scenario[EDGE80] = "http:000"
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "Edge :80 not responding" in result.stdout
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
    scenario["TS_STATUS_OUT"] = ""  # node line omitted entirely
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "missing" in result.stdout
    assert "ip-208-84-102-245" in result.stdout
    assert "RESULT: VPS_NOT_READY" in result.stdout


# ── Legacy opt-in keeps the old .243 semantics available ────────────────────


def test_legacy_opt_in_green_includes_retired_stack_checks(bin_dir: Path) -> None:
    scenario = _green()
    scenario.update(
        {
            "PROBE_LEGACY_243": "1",
            "VPS_PUBLIC": "208.84.102.243",
            "VPS_TS_IP": "100.109.142.4",
            "KESTRA_TS_IP": "100.75.34.75",
            "TS_NODE_VPS": "ip-208-84-102-243",
            "TS_NODE_KESTRA": "kestra-on-vps",
            LEGACY_SSH22: "ok",
            LEGACY_KESTRA_PUB: "http:200",
            LEGACY_COUCH_TS: 'body:{"couchdb":"Welcome","version":"3.3.2"}',
            "TS_STATUS_OUT": (
                "100.109.142.4   ip-208-84-102-243  root@ -\n"
                "100.75.34.75    kestra-on-vps      root@ -\n"
            ),
        }
    )
    result = _run(bin_dir, scenario)
    assert result.returncode == 0, result.stdout
    assert "RESULT: VPS_RECOVERED" in result.stdout
    assert "Kestra HTTP responds" in result.stdout
    assert "CouchDB responds" in result.stdout
    assert "0 failed" in result.stdout


def test_legacy_opt_in_kestra_down_blocks(bin_dir: Path) -> None:
    scenario = _green()
    scenario.update(
        {
            "PROBE_LEGACY_243": "1",
            "VPS_PUBLIC": "208.84.102.243",
            "VPS_TS_IP": "100.109.142.4",
            "KESTRA_TS_IP": "100.75.34.75",
            "TS_NODE_VPS": "ip-208-84-102-243",
            "TS_NODE_KESTRA": "kestra-on-vps",
            LEGACY_SSH22: "ok",
            LEGACY_KESTRA_PUB: "refuse",
            LEGACY_COUCH_TS: "refuse",
            "TS_STATUS_OUT": (
                "100.109.142.4   ip-208-84-102-243  root@ -\n"
                "100.75.34.75    kestra-on-vps      root@ -\n"
            ),
        }
    )
    result = _run(bin_dir, scenario)
    assert result.returncode == 1
    assert "Kestra :8080 closed" in result.stdout
    assert "RESULT: VPS_NOT_READY" in result.stdout
    # Host itself reachable: ICMP/SSH still pass.
    assert "ICMP replies" in result.stdout
    assert "SSH :22 open" in result.stdout


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
