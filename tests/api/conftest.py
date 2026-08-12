"""Shared pytest config for the API (``tests/api``) suite.

These tests exercise the chat engine against the deterministic fake provider —
they are **synthetic traffic** by intent. Two composing controls gate real-user
traffic on the chat path, both defaulting to the safe (refusing) direction in
code so production can never accidentally serve the deceased-persona voice
pre-flip:

* Stage 0.7 hard kill switch ``PERSONA_CHAT_REAL_USER_TRAFFIC`` (HU-1462,
  MANDATORY) — defaults to ``off``; refuses every real-user turn with 503.
* Stage 0.1 ramp gate ``PERSONA_CHAT_REAL_USER_MODE`` (HU-1444) — defaults to
  ``off``; refuses real-user turns with a warm 200.

To keep the engine suite exercisable without plumbing an internal-traffic
header into every POST, both defaults are overridden to the *allowing*
direction here: ``PERSONA_CHAT_REAL_USER_TRAFFIC=on`` +
``PERSONA_CHAT_REAL_USER_MODE=open``. This runs before any ``Settings()`` /
``get_settings()`` call, so the process-cached settings pick them up. The
gates themselves are covered by ``test_real_user_kill_switch.py`` and
``test_real_user_gate.py``, which pass explicit ``Settings(...)`` instances
per test (constructor kwargs win over env in pydantic-settings, so those
tests control their own state regardless of these defaults).
"""

from __future__ import annotations

import os

# Synthetic traffic by default for the engine suite. ``setdefault`` lets an
# explicit override from a gate test still win.
os.environ.setdefault("PERSONA_CHAT_REAL_USER_MODE", "open")
# Stage 0.7 hard kill switch — ON so the ramp gate / guardrails are reachable
# by the engine suite. The kill switch itself is exercised with explicit
# ``Settings(...)`` in test_real_user_kill_switch.py.
os.environ.setdefault("PERSONA_CHAT_REAL_USER_TRAFFIC", "on")
