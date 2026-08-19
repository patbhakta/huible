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

# Synthetic traffic by default for the e2e suite. ``setdefault`` lets an
# explicit override from a gate test still win.
os.environ.setdefault("PERSONA_CHAT_REAL_USER_MODE", "open")
# Stage 0.7 hard kill switch — ON so the ramp gate / guardrails are reachable
# by the e2e suite. The kill switch itself is exercised with explicit
# ``Settings(...)`` in tests/api/test_real_user_kill_switch.py.
os.environ.setdefault("PERSONA_CHAT_REAL_USER_TRAFFIC", "on")
