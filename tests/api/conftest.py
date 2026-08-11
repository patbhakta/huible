"""Shared pytest config for the API (``tests/api``) suite.

These tests exercise the chat engine against the deterministic fake provider —
they are **synthetic traffic** by intent. The real-user ramp gate / kill switch
(Stage 0.1, HU-1444) defaults to ``off`` in code so production can never
accidentally serve the deceased-persona voice pre-flip. To keep the engine
suite exercisable without plumbing an internal-traffic header into every POST,
the default mode for these test apps is ``open``.

This runs before any ``Settings()`` / ``get_settings()`` call, so the
process-cached settings pick up ``open``. The gate itself is covered by
``test_real_user_gate.py``, which passes explicit ``Settings(...)`` instances
per test (constructor kwargs win over env in pydantic-settings, so those tests
control their own mode regardless of this default).
"""

from __future__ import annotations

import os

# Synthetic traffic by default for the engine suite. ``setdefault`` lets an
# explicit override from a gate test still win.
os.environ.setdefault("PERSONA_CHAT_REAL_USER_MODE", "open")
