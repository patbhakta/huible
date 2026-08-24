"""Shared pytest config for the ``tests/e2e`` suite.

The e2e harness (``test_chandler_speaks.py``) drives the **persona-scoped**
chat surface — the single safety-stacked chat route (HU-1926 consolidation).
That route composes two real-user gates that both default to the safe
(refusing) direction in code:

* Stage 0.7 hard kill switch ``PERSONA_CHAT_REAL_USER_TRAFFIC`` (HU-1462)
  — defaults to ``off``; refuses every real-user turn with 503.
* Stage 0.1 ramp gate ``PERSONA_CHAT_REAL_USER_MODE`` (HU-1444) — defaults
  to ``off``; refuses real-user turns with a warm 200.

These tests exercise the chat engine with the deterministic fake provider —
they are **synthetic traffic** by intent. Before the HU-1926 consolidation
the harness ran on the generic ``POST /api/v1/chat``, which sat outside both
gates (the safety gap this issue closed); on the scoped surface the suite
must run with the gates in the allowing direction, mirroring
``tests/api/conftest.py``. The gates themselves are covered by
``tests/api/test_real_user_gate.py`` / ``test_real_user_kill_switch.py``,
which pass explicit ``Settings(...)`` instances per test (constructor kwargs
win over env in pydantic-settings).
"""

from __future__ import annotations

import os
import socket

import pytest

# Synthetic traffic by default for the e2e suite. ``setdefault`` lets an
# explicit override from a gate test still win.
os.environ.setdefault("PERSONA_CHAT_REAL_USER_MODE", "open")
# Stage 0.7 hard kill switch — ON so the ramp gate / guardrails are reachable
# by the e2e suite. The kill switch itself is exercised with explicit
# ``Settings(...)`` in tests/api/test_real_user_kill_switch.py.
os.environ.setdefault("PERSONA_CHAT_REAL_USER_TRAFFIC", "on")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail fast when the configured database host cannot be resolved.

    The durable §7.4 safety backends dial lazily on the first request, so a
    ``DATABASE_URL`` pointing at the compose-internal hostname ``postgres``
    used to surface as ten per-test ``failed to resolve host 'postgres'``
    reds — an environment red that reads like a code regression (the exact
    confusion behind the HU-1402 reopen). One upfront DNS probe turns that
    into a single actionable session error. The check mirrors
    ``Settings.effective_database_url`` / ``effective_safety_database_url``
    (admissible schemes only), so no DB configured means no probe and the
    in-memory backends keep the suite green.
    """
    from urllib.parse import urlparse

    from huible.api.settings import Settings

    settings = Settings()
    targets: set[tuple[str, int]] = set()
    for url in (settings.effective_database_url, settings.effective_safety_database_url):
        if not url:
            continue
        parsed = urlparse(url)
        if not parsed.hostname:
            continue
        try:
            port = parsed.port
        except ValueError:
            port = None
        targets.add((parsed.hostname, port if port is not None else 5432))

    for host, port in sorted(targets):
        try:
            socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise pytest.UsageError(
                f"e2e database unreachable: failed to resolve host {host!r} ({exc}). "
                "DATABASE_URL likely points at the compose-internal hostname "
                "'postgres', which only resolves inside the huible-net network. "
                "Run the suite against a reachable isolated Postgres — see "
                "README.md, 'Run the e2e Chandler-speaks harness on the HOST' "
                "(ephemeral pgvector on 127.0.0.1:55432 + alembic upgrade + "
                "DATABASE_URL env override). Never point it at the production "
                "huible-postgres."
            ) from exc
