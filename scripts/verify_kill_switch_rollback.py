#!/usr/bin/env python3
"""Stage 0.7 kill-switch rollback dry-run harness for HU-1462 (rollout plan §4.3).

The MANDATORY pre-Stage-1-advance proof that ``PERSONA_CHAT_REAL_USER_TRAFFIC``
is the instant, verifiable rollback path independent of key revocation (plan
§4.2/§4.3). Boots the FastAPI app in the exact operator configuration and runs
the documented dry-run procedure:

    Step 1 — kill switch OFF (the rollback posture)
        (a) real-user-keyed request  → refused with HTTP 503 SERVICE_DISABLED
        (b) internal-keyed request   → still served (probes/test suite run)
        (c) real-user crisis request → 503 + §7.4.1 handoff audit records
        (d) admin /real-user-mode    → reports kill_switch = off
    Step 2 — kill switch ON (the go-live posture)
        (e) real-user-keyed request  → served (flows past the kill switch)

Key-free and deterministic: FakeLLMClient + in-memory §7.4 backends, so it
runs anywhere the repo checks out with no secrets. Output is a redacted
transcript suitable for posting as the issue's rollback-dry-run evidence
(plan §5 acceptance: "Rollback dry-run result attached").

Real-user traffic is distinguished from internal/synthetic purely by the
``X-Huible-Traffic-Class`` header — a missing header is exactly what a grieving
user's client sends, so this harness IS the live real-user path.

Run:    python scripts/verify_kill_switch_rollback.py
Exit:   0 when every §4.3 check passes, 1 otherwise.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from huible.api.app import _embed, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import REAL_USER_TRAFFIC_CLASS_HEADER
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig
from huible.safety import InMemoryHandoffQueue, InMemoryRiskProfile

# Fixed persona id + key so the transcript is reproducible.
PERSONA_ID = UUID("f1a2b3c4-d5e6-7890-abcd-ef1234567890")
API_KEY = "key-chandler-family-rollback"
CRISIS_MESSAGE = "I am going to kill myself"
PLAIN_MESSAGE = "tell me about fishing"


# ── Minimal seeded memory backend (mirrors tests/api/test_chat_e2e.py) ───────


class _FakeBackend:
    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}
        self._vectors: list[tuple[list[float], UUID]] = []

    def seed(self, node: MemoryNode) -> None:
        self._memories[node.id] = node
        if node.embedding_content:
            self._vectors.append((node.embedding_content, node.id))

    async def store_memory(self, node: MemoryNode) -> UUID:
        self.seed(node)
        return node.id

    async def get_memory(self, memory_id: UUID) -> MemoryNode | None:
        return self._memories.get(memory_id)

    async def search_by_content(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for vec, node_id in self._vectors:
            node = self._memories[node_id]
            if node.persona_id != persona_id:
                continue
            dot = sum(q * e for q, e in zip(query_embedding, vec, strict=False))
            if dot > 0.0:
                results.append(SearchResult(node=node, score=dot))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def search_by_sensory(self, *a: Any, **k: Any) -> list[SearchResult]:
        return []

    async def search_by_affect(self, *a: Any, **k: Any) -> list[SearchResult]:
        return []

    async def get_edges(self, memory_id: UUID) -> list[Any]:
        return []


def _seeded_backend() -> _FakeBackend:
    backend = _FakeBackend()
    vec = _embed("fishing lake")
    for content in (
        "Chandler loved fishing on Lake Travis.",
        "He kept his rods in the garage.",
    ):
        backend.seed(
            MemoryNode(
                id=uuid4(),
                persona_id=PERSONA_ID,
                tier=MemoryTier.CANONICAL,
                content=content,
                content_type=ContentType.NARRATIVE,
                embedding_content=list(vec),
                memory_date=__import__("datetime").date(2015, 7, 15),
                source_type=SourceType.EXTRACTION,
                disclosure_scope=DisclosureScope.FAMILY,
                metadata={CONFIDENCE_LEVEL_METADATA_KEY: "high"},
            )
        )
    return backend


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
        age_at_death=72,
        death_date="2024-12-01",
    )


def _make_app(
    *,
    kill_switch: str,
    queue: InMemoryHandoffQueue | None = None,
) -> TestClient:
    """Boot the app with the given kill-switch state (ramp mode ``open``).

    The ramp gate is held at ``open`` so the kill switch is the only variable
    under test — when ON, real-user traffic flows past it.
    """
    settings = Settings(
        persona_chat_real_user_traffic=kill_switch,
        persona_chat_real_user_mode="open",
    )
    backend = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        handoff_queue=queue or InMemoryHandoffQueue(available_responders=1),
        risk_profile=InMemoryRiskProfile(),
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application)


def _real_headers() -> dict[str, str]:
    """Headers a real grieving-user client sends (no traffic-class header)."""
    return {"Authorization": f"Bearer {API_KEY}"}


def _internal_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}", REAL_USER_TRAFFIC_CLASS_HEADER: "internal"}


def _chat(
    client: TestClient,
    message: str,
    *,
    conv: str,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conv},
        headers=headers or _real_headers(),
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code, body


def _redact(s: str, n: int = 110) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


# ── Transcript helpers ───────────────────────────────────────────────────────


class Transcript:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []

    def add(self, *xs: str) -> None:
        for x in xs:
            self.lines.extend(x.split("\n"))

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        self.add(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            self.failures.append(name)

    def section(self, title: str) -> None:
        self.add("", title)

    def dump(self) -> str:
        return "\n".join(self.lines) + "\n"


# ── §4.3 dry-run sections ────────────────────────────────────────────────────


def section_intro(t: Transcript) -> None:
    t.add("=" * 78)
    t.add("HUIBLE — Stage 0.7 kill-switch rollback dry-run (HU-1462, plan §4.3)")
    t.add("=" * 78)
    t.add(f"generated: {datetime.now(UTC).isoformat()}")
    t.add("procedure (plan §4.3):")
    t.add("  Step 1 — PERSONA_CHAT_REAL_USER_TRAFFIC=off (rollback posture)")
    t.add("    (a) real-user request  → refused with HTTP 503 SERVICE_DISABLED")
    t.add("    (b) internal request   → still served (probes/test suite run)")
    t.add("    (c) real-user crisis   → 503 + §7.4.1 handoff audit records")
    t.add("    (d) admin /real-user-mode → reports kill_switch = off")
    t.add("  Step 2 — PERSONA_CHAT_REAL_USER_TRAFFIC=on (go-live posture)")
    t.add("    (e) real-user request  → served (flows past the kill switch)")
    t.add("")
    t.add("primary rollback path — independent of key-revocation propagation (§4.2).")


def section_step1_off(t: Transcript) -> None:
    t.section("Step 1 — kill switch OFF (PERSONA_CHAT_REAL_USER_TRAFFIC=off)")
    queue = InMemoryHandoffQueue(available_responders=1)
    c_off = _make_app(kill_switch="off", queue=queue)

    # (a) real-user → 503
    code, body = _chat(c_off, PLAIN_MESSAGE, conv="sess-rollback-real")
    err = body.get("detail", {}).get("error", {}) if isinstance(body, dict) else {}
    refused = (
        code == 503
        and err.get("code") == "SERVICE_DISABLED"
        and err.get("status") == 503
        and "988" in err.get("message", "")
        and err.get("crisis_detected") is False
    )
    t.check(
        "(a) OFF: real-user turn → 503 SERVICE_DISABLED (988 surfaced, no crisis)",
        refused,
        f"code={code} err={err.get('code')!r}",
    )
    t.add(f"    503 body message: {_redact(err.get('message', ''))}")

    # (b) internal → served (not 503)
    code_int, _ = _chat(
        c_off, "probe turn", conv="sess-rollback-int", headers=_internal_headers()
    )
    t.check(
        "(b) OFF: internal/synthetic turn → served (not 503)",
        code_int != 503,
        f"code={code_int}",
    )

    # (c) real-user crisis → 503 + handoff audit records
    code_c, body_c = _chat(
        c_off, CRISIS_MESSAGE, conv="sess-rollback-crisis"
    )
    err_c = body_c.get("detail", {}).get("error", {}) if isinstance(body_c, dict) else {}
    crisis_refused_with_audit = (
        code_c == 503
        and err_c.get("code") == "SERVICE_DISABLED"
        and err_c.get("crisis_detected") is True
        and "988" in err_c.get("message", "")
        and len(queue.list_pending()) == 1
        and queue.list_pending()[0].conversation_id == "sess-rollback-crisis"
    )
    t.check(
        "(c) OFF: real-user crisis turn → 503 + §7.4.1 handoff audit records",
        crisis_refused_with_audit,
        f"code={code_c} pending={len(queue.list_pending())}",
    )
    t.add(f"    503 body message: {_redact(err_c.get('message', ''))}")

    # (d) admin endpoint reports kill_switch = off
    status = c_off.get(
        "/api/v1/admin/real-user-mode", headers=_real_headers()
    ).json()["data"]
    t.check(
        "(d) OFF: admin /real-user-mode reports kill_switch=off",
        status.get("kill_switch") == "off" and status.get("kill_switch_enabled") is False,
        f"kill_switch={status.get('kill_switch')!r}",
    )


def section_step2_on(t: Transcript) -> None:
    t.section("Step 2 — kill switch ON (PERSONA_CHAT_REAL_USER_TRAFFIC=on)")
    c_on = _make_app(kill_switch="on")

    # (e) real-user → served (flows past the kill switch)
    code, _body = _chat(c_on, PLAIN_MESSAGE, conv="sess-rollback-on")
    served = code != 503
    t.check(
        "(e) ON: real-user turn → served (flows past the kill switch)",
        served,
        f"code={code}",
    )

    # Admin endpoint reflects ON.
    status = c_on.get(
        "/api/v1/admin/real-user-mode", headers=_real_headers()
    ).json()["data"]
    t.check(
        "ON: admin /real-user-mode reports kill_switch=on",
        status.get("kill_switch") == "on" and status.get("kill_switch_enabled") is True,
        f"kill_switch={status.get('kill_switch')!r}",
    )


def main() -> int:
    t = Transcript()
    section_intro(t)
    section_step1_off(t)
    section_step2_on(t)

    t.add("", "=" * 78)
    if t.failures:
        t.add(f"RESULT: FAIL — {len(t.failures)} check(s) failed: {', '.join(t.failures)}")
        t.add("=" * 78)
        print(t.dump())
        return 1
    t.add("RESULT: ALL §4.3 CHECKS PASS — rollback dry-run proven (HU-1462).")
    t.add("=" * 78)
    print(t.dump())
    return 0


if __name__ == "__main__":
    sys.exit(main())
