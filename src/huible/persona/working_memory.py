"""W4 working memory (HU-2309 v1.8 §1.7.2 / M-0R-B): TencentDB client.

BEAM Arm A (HU-1899 winner, deployed prod read path since HU-1917) is ported
into the chat path here. The ``HISTORY_WINDOW`` tail in the prompt keeps the
last few turns verbatim; long-range session state lives in TencentDB as real
working memory (V2 point 4) — per-block session gists (the digest) plus
session-scoped verbatim drill-down excerpts. This kills the RC-3 eviction
failure: ``HISTORY_WINDOW=10`` had forgotten session turn 1 by ~turn 22, so
"what was the first thing I said to you?" was answered wrong at E0 turn-34.

Isolation doctrine (2026-08-16 cross-chat contamination incident): every
session_key is namespaced under ``huible-`` and scoped per (persona,
conversation) via :func:`working_memory_session_key`. Arm A recall channels
are session-scoped inside the gateway, so one conversation's working memory
can never surface in another's prompt.

Failure doctrine (clinical path): working memory is an *enhancement* lane.
Every network / protocol failure degrades to "no working memory this turn"
(logged, surfaced on the trace) and must never break a persona turn — the
same posture as the W2 lexical lane and the deflection-exemplar cache.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "ARM_A_STRATEGY",
    "NullWorkingMemory",
    "TencentWorkingMemory",
    "WorkingMemoryClient",
    "WorkingMemoryRecall",
    "working_memory_session_key",
]

logger = logging.getLogger(__name__)

#: The gateway ``strategy`` value identifying the deployed BEAM Arm A read
#: path (gist digest + verbatim excerpts). Any other strategy (v3 fallback)
#: means the session digest is not part of the payload; the caller treats it
#: as plain excerpts and reports the strategy on the trace.
ARM_A_STRATEGY = "v4-arm-a"

#: Gateway hard limit is 8192 chars/message; we stay under it (same cap the
#: hermes mirror uses).
_MAX_CONTENT_CHARS = 8000


def working_memory_session_key(persona_id: Any, conversation_id: str) -> str:
    """Stable working-memory session key for a (persona, conversation) pair.

    The ``huible-`` namespace keeps the engine's sessions disjoint from the
    BEAM eval scopes (``beam-…``) and the hermes mirrors; the persona id
    keeps two personas that share a conversation id from sharing memory.
    """
    return f"huible-p{persona_id}-c{conversation_id}"


@dataclass(slots=True)
class WorkingMemoryRecall:
    """One recall result, evidence + prompt content separated.

    ``context`` is the gateway's ``prepend_context`` payload — the Arm A
    conversation digest (session gists) plus verbatim drill-down excerpts.
    It is *prompt content* (rendered in the WORKING MEMORY section).
    ``strategy`` and ``chars`` are *evidence* (trace observability only).
    """

    context: str
    strategy: str
    chars: int

    @classmethod
    def empty(cls) -> WorkingMemoryRecall:
        return cls(context="", strategy="", chars=0)


class WorkingMemoryClient(Protocol):
    """Structural type satisfied by the Tencent client, the null impl, and tests."""

    async def recall(self, session_key: str, query: str) -> WorkingMemoryRecall:
        """Return working memory for the session; ``empty()`` on any failure."""
        ...

    async def capture(self, session_key: str, user_content: str, assistant_content: str) -> bool:
        """Record a completed turn; ``False`` on any failure (logged)."""
        ...


class NullWorkingMemory:
    """Disabled lane (``WORKING_MEMORY_ENABLED=off``): recall is always empty."""

    async def recall(self, session_key: str, query: str) -> WorkingMemoryRecall:
        return WorkingMemoryRecall.empty()

    async def capture(self, session_key: str, user_content: str, assistant_content: str) -> bool:
        return False


class TencentWorkingMemory:
    """TencentDB MemoryCore gateway client (v1 ``/recall`` + ``/capture``).

    ``/recall`` returns the deployed v4 Arm A payload: ``prepend_context`` =
    session-gist digest + session-scoped verbatim excerpts; ``strategy``
    names the read path actually used (``v4-arm-a`` or a v3 fallback).
    ``/capture`` commits a completed user+assistant turn to L0 and notifies
    the extraction pipeline (the same write path hermes' ``sync_turn`` uses).

    Both calls are bounded by ``timeout_s`` and never raise: a recall failure
    yields :meth:`WorkingMemoryRecall.empty`, a capture failure yields
    ``False``. The chat path stays clinically available when the memory
    gateway is down (degraded = today's pre-W4 behavior).
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        service_id: str = "default",
        timeout_s: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._service_id = service_id
        self._timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "x-tdai-service-id": self._service_id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """One bounded POST; parsed JSON body or ``None`` on any failure."""
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._base_url + path,
            data=body,
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            logger.warning("working-memory POST %s failed: %s", path, exc)
            return None
        try:
            parsed: dict[str, Any] = json.loads(raw or "{}")
        except json.JSONDecodeError:
            logger.warning("working-memory POST %s returned non-JSON body", path)
            return None
        return parsed

    async def recall(self, session_key: str, query: str) -> WorkingMemoryRecall:
        """Arm A recall for the session; empty on any failure (degrade, don't raise)."""
        if not session_key or not query:
            return WorkingMemoryRecall.empty()
        parsed = self._post(
            "/recall",
            {"query": query[:2000], "session_key": session_key},
        )
        if parsed is None:
            return WorkingMemoryRecall.empty()
        # code != 0 means the recall path itself failed (H-15); the HTTP
        # status was still 200 — treat as "no working memory this turn".
        if parsed.get("code", 0) != 0:
            logger.warning(
                "working-memory recall failed: code=%s msg=%r",
                parsed.get("code"),
                parsed.get("message"),
            )
            return WorkingMemoryRecall.empty()
        context = parsed.get("prepend_context") or ""
        return WorkingMemoryRecall(
            context=context,
            strategy=str(parsed.get("strategy") or ""),
            chars=len(context),
        )

    async def capture(self, session_key: str, user_content: str, assistant_content: str) -> bool:
        """Commit the completed turn to L0; False on failure (never raises)."""
        if not session_key or not user_content or not assistant_content:
            return False
        parsed = self._post(
            "/capture",
            {
                "user_content": user_content[:_MAX_CONTENT_CHARS],
                "assistant_content": assistant_content[:_MAX_CONTENT_CHARS],
                "session_key": session_key,
            },
        )
        if parsed is None or parsed.get("code", 0) != 0:
            logger.warning(
                "working-memory capture failed for session %s", session_key
            )
            return False
        return True
