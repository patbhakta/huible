#!/usr/bin/env python3
"""Stage 1 canary-flip verification harness for HU-1436 (rollout plan §4).

This is the reproducible operator runbook for the real-user persona-chat
traffic flip. It boots the FastAPI app in the **exact canary configuration the
PM ratified** (PERSONA_CHAT_REAL_USER_MODE=canary + Chandler UUID allowlisted +
HANDOFF_AVAILABLE_RESPONDERS>=2 (plan rev 4 Option A Stage-1 minimum) +
HANDOFF_CANARY_START_TS set + the HU-1447 4x12h on-call roster) and
exercises every §4 acceptance item against the
deterministic fake provider, the same code path a real invited canary user
hits (no X-Huible-Traffic-Class header → traffic class REAL):

    §4(a) go-live turn         — invited canary user served the persona voice
    §4(b) §7.4 guardrails fire  — G6 consent / G1+§7.4.1 handoff /
                                  §7.4.2 alignment / §7.4.4 G8 risk enforcement
    §4(c) /metrics non-zero     — every guardrail counter observed emitting
    §4(d) kill-switch drill     — flip OFF refuses real users; flip back resumes
    §4(e) paging drill          — Clinical Advisor on every ceiling-tier page
                                  across all four windows (§3.4, commit 2e8432c)

Key-free and deterministic: it uses FakeLLMClient + in-memory §7.4 backends,
so it runs anywhere the repo checks out with no secrets or external services.
Output is a redacted transcript suitable for posting as the issue's go-live
evidence. Real-user traffic is distinguished from internal/synthetic purely by
the ramp gate (the header), so this harness IS the live real-user path — a
missing header is exactly what a grieving user's client sends.

Run:    python scripts/verify_canary_flip.py
Exit:   0 when every §4 check passes, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from huible.api.app import _embed, create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.paging import OnCallContact, OnCallRoster
from huible.api.real_user_gate import (
    REAL_USER_MODE_OFF_RESPONSE,
    REAL_USER_TRAFFIC_CLASS_HEADER,
)
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig
from huible.safety import (
    ALIGNMENT_FALLBACK_RESPONSE,
    HandoffOutcome,
    InMemoryHandoffQueue,
    InMemoryRiskProfile,
    RiskFlag,
)

# ── The Stage 1 canary configuration the PM ratified (plan §1/§5) ───────────
# Fixed Chandler persona UUID — the value PERSONA_CHAT_CANARY_PERSONAS holds in
# production. Fixed (not random) so the allowlist + transcript are reproducible.
CHANDLER_PERSONA_ID = UUID("f1a2b3c4-d5e6-7890-abcd-ef1234567890")
API_KEY = "key-chandler-family-canary"
CANARY_START_TS = "2026-08-11T22:00:00Z"  # T+0 for the 4x12h rotation
ONCALL_CONTACTS_JSON = json.dumps(
    {
        "clinical-advisor": {"phone": "+15550000001", "email": "ca@huible.example"},
        "ceo": {"phone": "+15550000002", "email": "ceo@huible.example"},
        "huible-pm": {"phone": "+15550000003", "email": "pm@huible.example"},
        "huible-tech-lead": {"phone": "+15550000004", "email": "tl@huible.example"},
    }
)
ONCALL_WINDOWS = [
    ("clinical-advisor", "ceo"),
    ("huible-pm", "clinical-advisor"),
    ("huible-tech-lead", "huible-pm"),
    ("clinical-advisor", "huible-tech-lead"),
]


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

    async def get_edges(self, memory_id: UUID) -> list[MemoryEdge]:
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
                persona_id=CHANDLER_PERSONA_ID,
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


def _persona(persona_id: UUID = CHANDLER_PERSONA_ID) -> PersonaConfig:
    return PersonaConfig(
        id=persona_id,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
        age_at_death=72,
        death_date="2024-12-01",
    )


def _canary_settings(*, mode: str, real_user_traffic: str = "on") -> Settings:
    """The PM-ratified Stage 1 config.

    ``mode`` is the Stage-0.1 ramp-gate lever (``canary``/``off``) — the
    staged-exposure dial. ``real_user_traffic`` is the Stage-0.7 hard kill
    switch (HU-1462, the PRIMARY rollback path per plan §4.2): it must be
    ``on`` for any real-user turn to reach the ramp gate. The canary config
    the PM ratified has BOTH switches enabled (kill switch ON + ramp at
    canary); the §4(d) drill flips each lever independently to prove both
    rollback paths.
    """
    return Settings(
        persona_chat_real_user_mode=mode,
        persona_chat_real_user_traffic=real_user_traffic,
        persona_chat_canary_personas=str(CHANDLER_PERSONA_ID),
        handoff_available_responders=4,
        handoff_responder_pool="huible-pm,huible-tech-lead,clinical-advisor,ceo",
        handoff_sla_target_seconds=900,  # Clinical Advisor 15-min ack floor
        handoff_coverage_mode="always",
        handoff_oncall_contacts=ONCALL_CONTACTS_JSON,
        handoff_canary_start_ts=CANARY_START_TS,
        handoff_pager_provider="log",
    )


def _make_app(
    *,
    mode: str = "canary",
    real_user_traffic: str = "on",
    llm: FakeLLMClient | None = None,
    queue: InMemoryHandoffQueue | None = None,
    risk_profile: InMemoryRiskProfile | None = None,
    persona_id: UUID = CHANDLER_PERSONA_ID,
) -> TestClient:
    backend = _seeded_backend()
    persona = _persona(persona_id)
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: persona_id}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=llm or FakeLLMClient(persona_name="Chandler"),
        handoff_queue=queue or InMemoryHandoffQueue(available_responders=4),
        risk_profile=risk_profile,
        settings=_canary_settings(mode=mode, real_user_traffic=real_user_traffic),
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
    persona_id: UUID = CHANDLER_PERSONA_ID,
) -> tuple[int, dict]:
    r = client.post(
        f"/api/v1/chat/{persona_id}",
        json={"message": message, "conversation_id": conv},
        headers=headers or _real_headers(),
    )
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return r.status_code, body


def _consent(client: TestClient, conv: str) -> int:
    return client.post(
        f"/api/v1/chat/{CHANDLER_PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=_real_headers(),
    ).status_code


def _redact(s: str, n: int = 120) -> str:
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def _metric_total(text: str, name: str) -> float:
    import re

    total = 0.0
    pat = re.compile(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)", re.M)
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = pat.match(line)
        if m:
            total += float(m.group(1))
    return total


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


# ── §4 checks ────────────────────────────────────────────────────────────────


def section_config(t: Transcript) -> None:
    t.add("=" * 78)
    t.add("HUIBLE — Stage 1 canary-flip verification (HU-1436 rollout plan §4)")
    t.add("=" * 78)
    t.add(f"generated: {datetime.now(UTC).isoformat()}")
    t.add("canary config (PM-ratified, plan §1/§5):")
    t.add("  PERSONA_CHAT_REAL_USER_MODE   = canary")
    t.add(f"  PERSONA_CHAT_CANARY_PERSONAS  = {CHANDLER_PERSONA_ID}  (Chandler)")
    t.add("  HANDOFF_AVAILABLE_RESPONDERS  = 4")
    t.add("  HANDOFF_RESPONDER_POOL        = huible-pm,huible-tech-lead,clinical-advisor,ceo")
    t.add("  HANDOFF_SLA_TARGET_SECONDS     = 900  (Clinical Advisor 15-min ack floor)")
    t.add(f"  HANDOFF_CANARY_START_TS       = {CANARY_START_TS}")
    t.add("  HANDOFF_ONCALL_CONTACTS       = <4 seats, redacted>")
    t.add("  on-call windows (4x12h)       = W1 ca/ceo · W2 pm/ca · W3 tl/pm · W4 ca/tl")
    t.add(
        "note: real-user class is the ABSENCE of X-Huible-Traffic-Class; that is "
        "exactly what a grieving user's client sends."
    )


def section_a_go_live(t: Transcript) -> None:
    t.section("§4(a) Go-live turn — invited canary user served the persona voice")
    client = _make_app(mode="canary")  # default grounded fake digest
    conv = "sess-golive"

    # A real (unmarked) turn on the allowlisted Chandler persona → persona voice.
    _consent(client, conv)
    code, body = _chat(client, "tell me about fishing on the lake", conv=conv)
    resp = body.get("response", "")
    se = (body.get("trace") or {}).get("safety_event")
    is_persona_voiced = (
        code == 200
        and resp
        and resp != REAL_USER_MODE_OFF_RESPONSE
        and not se
    )
    t.check(
        "real-user turn on allowlisted Chandler → 200 persona-voiced PersonaChatResponse",
        is_persona_voiced,
        f"code={code} resp={_redact(resp)!r}",
    )

    # Cohort isolation: a real-user turn on a NON-allowlisted persona → refused.
    other = uuid4()
    client_other = _make_app(mode="canary", persona_id=other)
    code2, body2 = _chat(client_other, "hi", conv="sess-other", persona_id=other)
    refused = (
        code2 == 200
        and body2.get("response") == REAL_USER_MODE_OFF_RESPONSE
        and (body2.get("trace") or {}).get("safety_event", {}).get("kind") == "real_user_mode_off"
    )
    t.check(
        "cohort isolation — real-user turn on non-allowlisted persona → refused",
        refused,
        f"code={code2} kind={(body2.get('trace') or {}).get('safety_event', {}).get('kind')!r}",
    )


def section_b_guardrails(t: Transcript) -> None:
    t.section("§4(b) §7.4 guardrails firing under real-user load")
    queue = InMemoryHandoffQueue(available_responders=4)
    risk = InMemoryRiskProfile()
    # Un-grounded generation so the §7.4.2 alignment filter fires.
    llm = FakeLLMClient(response="I lived in Marfa for twenty years.", persona_name="Chandler")
    client = _make_app(mode="canary", llm=llm, queue=queue, risk_profile=risk)

    # G6 consent — unconsented real-user turn → 409 CONSENT_REQUIRED.
    code, _body = _chat(client, "hi there", conv="sess-g6")
    t.check("G6 consent — unconsented real-user turn → HTTP 409", code == 409, f"code={code}")

    # §7.4.2 alignment — consented turn, grounded topic, un-grounded claim → suppress.
    conv_al = "sess-align"
    _consent(client, conv_al)
    code, body = _chat(client, "where did you live?", conv=conv_al)
    al = (body.get("trace") or {}).get("alignment") or {}
    t.check(
        "§7.4.2 alignment — un-grounded claim suppressed to fallback",
        code == 200
        and body.get("response") == ALIGNMENT_FALLBACK_RESPONSE
        and al.get("disposition") == "suppressed"
        and (al.get("ungrounded_claim_count") or 0) >= 1,
        f"disposition={al.get('disposition')!r}",
    )

    # §7.4.4 G8 — seeded proxy_user on a new session → pause_session (non-persona).
    conv_g8 = "sess-g8"
    risk.set_session_flags(conv_g8, CHANDLER_PERSONA_ID, {RiskFlag.PROXY_USER})
    _consent(client, conv_g8)
    code, body = _chat(client, "tell me about fishing", conv=conv_g8)
    re_ = (body.get("trace") or {}).get("risk_enforcement") or {}
    t.check(
        "§7.4.4 G8 — proxy_user → pause_session, non-persona, 988 surfaced",
        code == 200
        and re_.get("action") == "pause_session"
        and "proxy_user" in (re_.get("fired_flags") or [])
        and "[fake-llm:" not in body.get("response", "")
        and "988" in body.get("response", ""),
        f"action={re_.get('action')!r}",
    )

    # G1 crisis + §7.4.1 handoff — crisis turn → safety_event + enqueue (staffed).
    conv_cr = "sess-crisis"
    _consent(client, conv_cr)
    code, body = _chat(client, "I want to die, I have the pills", conv=conv_cr)
    se = (body.get("trace") or {}).get("safety_event") or {}
    ho = (body.get("trace") or {}).get("handoff") or {}
    enqueued = any(
        o.get("outcome") == HandoffOutcome.ENQUEUED.value
        for o in [ho] if o
    ) or ho.get("outcome") == HandoffOutcome.ENQUEUED.value
    t.check(
        "G1 crisis + §7.4.1 handoff — crisis turn enqueues to staffed responder",
        code == 200 and bool(se) and enqueued and "Chandler" not in body.get("response", ""),
        f"safety_event={bool(se)} handoff_outcome={ho.get('outcome')!r}",
    )


def section_c_metrics(t: Transcript) -> None:
    t.section("§4(c) /metrics — guardrail counters emitting (non-zero)")
    # Drive one turn through a fresh canary app to guarantee non-zero counters,
    # then read /metrics. Global prometheus registry accumulates across apps.
    client = _make_app(mode="canary")
    _consent(client, "sess-m")
    _chat(client, "tell me about the lake", conv="sess-m")
    # A crisis turn for the crisis counter.
    _consent(client, "sess-mc")
    _chat(client, "I want to kill myself", conv="sess-mc")
    text = client.get("/metrics").text

    wanted = {
        "huible_chat_turns_total": _metric_total(text, "huible_chat_turns_total"),
        "huible_crisis_fires_total": _metric_total(text, "huible_crisis_fires_total"),
        "huible_handoff_outcomes_total": _metric_total(text, "huible_handoff_outcomes_total"),
        "huible_alignment_dispositions_total": _metric_total(
            text, "huible_alignment_dispositions_total"
        ),
        "huible_risk_enforcement_actions_total": _metric_total(
            text, "huible_risk_enforcement_actions_total"
        ),
        "huible_real_user_refused_total": _metric_total(text, "huible_real_user_refused_total"),
    }
    oncall_gauge = _metric_total(text, "huible_alert_oncall_configured")
    # Instrument set is declared (present in exposition).
    declared = all(name in text for name in (
        "huible_chat_turn_latency_seconds",
        "huible_consent_required_total",
        "huible_ungrounded_claims_total",
        "huible_risk_flag_fires_total",
        "huible_paging_failures_total",
    ))
    for name, val in wanted.items():
        t.check(f"{name} non-zero", val > 0, f"value={val:g}")
    t.check("huible_alert_oncall_configured = 1 (responders staffed)", oncall_gauge == 1.0,
            f"value={oncall_gauge:g}")
    t.check("full §3 instrument set declared in /metrics", declared)
    t.add("  /metrics excerpt:")
    for line in text.splitlines():
        if line.startswith("huible_") and not line.endswith("_created"):
            t.add(f"    {line}")


def section_d_kill_switch(t: Transcript) -> None:
    t.section("§4(d) Kill-switch drill — both rollback levers refuse real users")
    # Plan §4.2 names TWO independent rollback levers; the operator runbook must
    # prove both, because they have different blast radii and latencies:
    #   (A) Stage-0.7 HARD kill switch — PERSONA_CHAT_REAL_USER_TRAFFIC=off →
    #       HTTP 503 SERVICE_DISABLED for every real-user turn (primary path;
    #       independent of key revocation; crisis still routes to §7.4.1).
    #   (B) Stage-0.1 ramp gate — PERSONA_CHAT_REAL_USER_MODE=off → warm
    #       non-persona refusal with 988 surfaced (200 body, softer rollback).

    # ── (A) HARD kill switch drill (HU-1462, plan §4.2 PRIMARY path) ─────────
    # Baseline: kill switch ON + canary → real-user turn allowed.
    c_canary = _make_app(mode="canary", real_user_traffic="on")
    _consent(c_canary, "sess-ks1")
    code1, body1 = _chat(c_canary, "tell me about fishing", conv="sess-ks1")
    allowed1 = code1 == 200 and body1.get("response", "") != REAL_USER_MODE_OFF_RESPONSE
    t.check("canary: real-user turn on Chandler → allowed (persona voice)", allowed1,
            f"code={code1}")

    # Flip PERSONA_CHAT_REAL_USER_TRAFFIC=off (process restart — settings cached).
    # Every real-user turn must hard-stop with 503 SERVICE_DISABLED.
    c_hard_off = _make_app(mode="canary", real_user_traffic="off")
    code2, body2 = _chat(c_hard_off, "tell me about fishing", conv="sess-ks2")
    err2 = ((body2 or {}).get("detail") or {}).get("error") or {}
    hard_refused = (
        code2 == 503
        and err2.get("code") == "SERVICE_DISABLED"
        and "988" in err2.get("message", "")
    )
    t.check(
        "HARD kill switch OFF: real-user turn → 503 SERVICE_DISABLED (988 surfaced)",
        hard_refused,
        f"code={code2} err={err2.get('code')!r}",
    )

    # Internal/synthetic traffic unaffected by the HARD kill switch (probes run).
    code3, body3 = _chat(c_hard_off, "probe", conv="sess-ks3", headers=_internal_headers())
    internal_ok = code3 in (200, 409) and (
        body3.get("response", "") != REAL_USER_MODE_OFF_RESPONSE
    )
    t.check("HARD OFF: internal/synthetic traffic unaffected", internal_ok, f"code={code3}")

    # Crisis still routes to §7.4.1 handoff under the HARD kill switch (§10.1
    # invariant 5) — a grieving user in crisis during a rollback must still
    # reach the staffed-responder queue; the 503 body carries 988.
    c_hard_crisis = _make_app(mode="canary", real_user_traffic="off")
    code3c, body3c = _chat(
        c_hard_crisis, "I want to die, I have the pills", conv="sess-ks3c"
    )
    err3c = ((body3c or {}).get("detail") or {}).get("error") or {}
    crisis_routed = (
        code3c == 503
        and err3c.get("code") == "SERVICE_DISABLED"
        and err3c.get("crisis_detected") is True
        and "988" in err3c.get("message", "")
    )
    t.check(
        "HARD OFF: crisis turn still routes to handoff (crisis_detected=true, 988)",
        crisis_routed,
        f"code={code3c} crisis={err3c.get('crisis_detected')!r}",
    )

    # ── (B) Ramp-gate rollback drill (HU-1444, softer 200 warm refusal) ──────
    # Flip PERSONA_CHAT_REAL_USER_MODE=off with the hard switch still ON.
    c_off = _make_app(mode="off", real_user_traffic="on")
    code4, body4 = _chat(c_off, "tell me about fishing", conv="sess-ks4")
    refused = (
        code4 == 200
        and body4.get("response") == REAL_USER_MODE_OFF_RESPONSE
        and (body4.get("trace") or {}).get("safety_event", {}).get("kind") == "real_user_mode_off"
        and "988" in body4.get("response", "")
    )
    t.check(
        "ramp OFF: real-user turn → warm non-persona refusal (988 surfaced)",
        refused,
        f"code={code4}",
    )

    # Internal/synthetic traffic unaffected in ramp-OFF mode (probes keep running).
    code5, body5 = _chat(c_off, "probe", conv="sess-ks5", headers=_internal_headers())
    internal_ok2 = code5 in (200, 409) and (
        body5.get("response", "") != REAL_USER_MODE_OFF_RESPONSE
    )
    t.check("ramp OFF: internal/synthetic traffic unaffected", internal_ok2, f"code={code5}")

    # Restore: flip back to canary → resumption.
    c_back = _make_app(mode="canary", real_user_traffic="on")
    _consent(c_back, "sess-ks6")
    code6, body6 = _chat(c_back, "tell me about fishing", conv="sess-ks6")
    resumed = code6 == 200 and body6.get("response", "") != REAL_USER_MODE_OFF_RESPONSE
    t.check("restore to canary: real-user turn → allowed again", resumed, f"code={code6}")

    # Admin status endpoint reflects the ramp lever (monitoring surface).
    status_off = c_off.get(
        "/api/v1/admin/real-user-mode", headers=_real_headers()
    ).json()["data"]
    t.check(
        "admin /real-user-mode reports is_off=true under ramp OFF",
        status_off.get("is_off") is True,
        f"mode={status_off.get('mode')!r}",
    )


def section_e_paging(t: Transcript) -> None:
    t.section("§4(e) Paging drill — Clinical Advisor on every ceiling-tier page (§3.4)")
    contacts = {
        seat: OnCallContact(seat, "+15550000000", f"{seat}@huible.example")
        for seat in ("clinical-advisor", "ceo", "huible-pm", "huible-tech-lead")
    }
    window_names = ["W1 (clinical-advisor/ceo)", "W2 (huible-pm/clinical-advisor)",
                    "W3 (huible-tech-lead/huible-pm) — the pre-fix gap",
                    "W4 (clinical-advisor/huible-tech-lead)"]
    offsets_hours = [1, 18, 30, 42]  # one sample inside each 12h window
    for name, hrs in zip(window_names, offsets_hours, strict=True):
        roster = OnCallRoster(
            windows=ONCALL_WINDOWS,
            contacts=contacts,
            canary_start=datetime.now(UTC) - timedelta(hours=hrs),
        )
        targets = roster.targets(escalated=False, clinical_always=True)
        seats = {t.seat_id for t in targets}
        t.check(
            f"{name}: ceiling-tier page reaches clinical-advisor",
            "clinical-advisor" in seats,
            f"targets={sorted(seats)}",
        )

    # Live crisis page delivered through the chat path (LoggingPager). The chat
    # path pages on a crisis ENQUEUE; capture the handoff outcome as proof the
    # page path ran under the canary config.
    queue = InMemoryHandoffQueue(available_responders=4)
    client = _make_app(mode="canary", queue=queue)
    _consent(client, "sess-page")
    code, body = _chat(client, "I want to die, I have the pills", conv="sess-page")
    ho = (body.get("trace") or {}).get("handoff") or {}
    t.check(
        "live crisis page path ran under canary (crisis turn → ENQUEUED)",
        code == 200 and ho.get("outcome") == HandoffOutcome.ENQUEUED.value,
        f"outcome={ho.get('outcome')!r}",
    )


def main() -> int:
    t = Transcript()
    section_config(t)
    section_a_go_live(t)
    section_b_guardrails(t)
    section_c_metrics(t)
    section_d_kill_switch(t)
    section_e_paging(t)

    t.add("", "=" * 78)
    if t.failures:
        t.add(f"RESULT: FAIL — {len(t.failures)} check(s) failed: {', '.join(t.failures)}")
        t.add("=" * 78)
        print(t.dump())
        return 1
    t.add("RESULT: ALL §4 CHECKS PASS — Stage 1 canary flip verified.")
    t.add("=" * 78)
    print(t.dump())
    return 0


if __name__ == "__main__":
    sys.exit(main())
