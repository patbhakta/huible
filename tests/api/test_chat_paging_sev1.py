"""Stage 0.4a §3 Sev-1 chat-path paging integration tests (HU-1451).

Exercises the new Sev-1 triggers wired into the persona-chat path:

* Trigger #2 — an un-grounded persona claim leak (the §7.4.2 alignment guard
  fires ``suppressed``) pages the on-call immediately, fire-and-forget.
* Trigger #4 — a consent-bypass (the G6 gate was somehow not recorded when the
  post-hoc check runs) pages the on-call immediately.
* The ``huible_paging_failures_total{trigger}`` counter increments when a real
  channel reports failures, and surfaces in ``/metrics`` — while the clinical
  turn itself is unaffected (the response is returned normally).
* A real ack-SLA miss escalates to the secondary + CEO seat via the roster.

Key-free: the deterministic ``_RecordingPager`` is the transport under test.
"""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.paging import (
    PAGE_SEVERITY_SEV1,
    PAGE_TRIGGER_CONSENT_BYPASS,
    PAGE_TRIGGER_DEGRADED_NET,
    PAGE_TRIGGER_UNGROUNDED_LEAK,
)
from huible.api.real_user_gate import REAL_USER_TRAFFIC_CLASS_HEADER
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.safety import (
    ConsentGate,
    ConsentRecord,
    HandoffTicket,
    InMemoryConsentGate,
    InMemoryHandoffQueue,
    InMemoryRiskProfile,
)
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID, _persona, _seeded_backend


class _RecordingPager:
    """Test-double pager that records every page + reports configurable failures."""

    def __init__(self, *, failures: int = 0) -> None:
        self.pages: list[tuple[str, str, str]] = []
        self.triggers: list[str] = []
        self._failures = failures

    def page(self, ticket: HandoffTicket, *, severity: str, window: str, **kwargs) -> int:
        self.pages.append((ticket.id, severity, window))
        self.triggers.append(kwargs.get("trigger", "unspecified"))
        return self._failures


class _BypassConsentGate:
    """Consent gate whose acknowledgement 'evaporates' after the first read.

    Models the §3 Sev-1 (C) bypass condition: the gate check during the chat
    turn passes (True), but the post-hoc re-check finds consent missing — e.g.
    a backend corruption / race / a future code path that skips recording.
    Records normally; ``is_acknowledged`` returns True exactly once per
    (session, persona), then False.
    """

    def __init__(self) -> None:
        self._inner = InMemoryConsentGate()
        self._reads: set[tuple[str, object]] = set()

    def is_acknowledged(self, session_id: str | None, persona_id: object) -> bool:
        key = (session_id or "", persona_id)
        if key in self._reads:
            return False  # the bypass: consent "evaporated" on the second read
        self._reads.add(key)
        return self._inner.is_acknowledged(session_id, persona_id)

    def record_acknowledgement(
        self, session_id: str | None, *, persona_id, card_version: str | None = None
    ) -> ConsentRecord:
        return self._inner.record_acknowledgement(
            session_id, persona_id=persona_id, card_version=card_version
        )


def _make_app(
    *,
    queue: InMemoryHandoffQueue | None = None,
    pager: _RecordingPager | None = None,
    risk_profile: InMemoryRiskProfile | None = None,
    consent_gate: ConsentGate | None = None,
    llm: object | None = None,
    responders: int = 0,
    settings: Settings | None = None,
) -> TestClient:
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    used_settings = settings or Settings()
    app = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=llm or FakeLLMClient(persona_name="Chandler"),
        handoff_queue=queue or InMemoryHandoffQueue(available_responders=responders),
        risk_profile=risk_profile,
        consent_gate=consent_gate,
        pager=pager,
        settings=used_settings,
        start_time=0.0,
    )
    return TestClient(app)


def _auth_headers(*, traffic: str = "internal") -> dict:
    return {"Authorization": f"Bearer {API_KEY}", REAL_USER_TRAFFIC_CLASS_HEADER: traffic}


def _consent(client: TestClient, conv: str) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=_auth_headers(),
    )
    assert r.status_code == 200, r.text


def _chat(client: TestClient, conv: str, message: str = "tell me about fishing on the lake"):
    return client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conv},
        headers=_auth_headers(),
    )


# --- Trigger #2: un-grounded claim leak ------------------------------------


class _JudgedLLM:
    """Real-provider-labelled double: scripted reply + scripted judge verdict.

    First ``generate`` (the turn) returns ``reply``; later calls — the HU-2161
    judge backstop, whose prompt begins with "Persona record:" — return the
    scripted ``judge_json``.
    """

    provider = "zai"

    def __init__(self, reply: str, judge_json: str) -> None:
        self._reply = reply
        self._judge_json = judge_json
        self.judge_calls = 0

    async def generate(self, prompt: str, *, system_prompt=None, **kwargs) -> str:
        if prompt.startswith("Persona record:"):
            self.judge_calls += 1
            return self._judge_json
        return self._reply


_FABRICATED_JOB_CLAIM = (
    "Before all this I worked the winter at the Kringlesmith refinery in Marfa."
)
_SUPPORTED_CANON_CLAIM = (
    "I spent nine years doing statistical analysis and data reconfiguration."
)


class TestUngroundedClaimLeakPage:
    def test_suppressed_alignment_guard_pages_sev1(self):
        """An un-grounded identity claim → alignment ``suppressed`` → Sev-1 page.

        The generator confabulated a *policy* claim (G2/H2 identity pattern) —
        a deterministic, high-confidence violation that never reaches the
        HU-2161 judge — so the detection alone is the §3 Sev-1 (A) signal.
        The user-facing turn still returns the claim-free fallback (paging
        never alters the response).
        """
        # Inject a fixed LLM reply carrying an identity claim (G2/H2 pattern).
        leaky_llm = FakeLLMClient(
            response="I am really here with you in person right now.",
            persona_name="Chandler",
        )
        pager = _RecordingPager()
        client = _make_app(pager=pager, llm=leaky_llm, responders=1)
        _consent(client, "sess-leak")
        r = _chat(client, "sess-leak")
        assert r.status_code == 200, r.text
        body = r.json()

        # The alignment guard substituted the claim-free fallback.
        assert body["trace"]["alignment"]["disposition"] == "suppressed"
        assert body["trace"]["alignment"]["ungrounded_claim_count"] >= 1
        # The Sev-1 page fired with the un-grounded-leak trigger.
        assert len(pager.pages) == 1
        assert pager.pages[0][1] == PAGE_SEVERITY_SEV1
        assert pager.triggers == [PAGE_TRIGGER_UNGROUNDED_LEAK]
        # The clinical turn was unaffected: the fallback text was returned.
        assert "really here" not in body["response"]

    def test_unconfirmed_bio_suppression_does_not_page(self):
        """HU-2161: a content-overlap bio-claim suppression with NO judge
        (fake generator) is unconfirmed — clinical-safe fallback returned,
        but never a Sev-1 page. The rationale counter surfaces it instead."""
        leaky_llm = FakeLLMClient(
            response=_FABRICATED_JOB_CLAIM, persona_name="Chandler"
        )
        pager = _RecordingPager()
        client = _make_app(pager=pager, llm=leaky_llm, responders=1)
        _consent(client, "sess-unconfirmed")
        r = _chat(client, "sess-unconfirmed")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["trace"]["alignment"]["disposition"] == "suppressed"
        assert "refinery" not in body["response"]
        # No page: an unconfirmed Phase-1 verdict is not Sev-1 evidence.
        assert pager.pages == []
        metrics = client.get("/metrics").text
        assert "huible_alignment_unconfirmed_suppressions_total" in metrics

    def test_judge_fabricated_bio_claim_suppresses_and_pages(self):
        """HU-2161: a judge-confirmed confabulation is high-confidence — the
        suppression stands AND the §3 Sev-1 (A) page fires."""
        judge_json = json.dumps(
            {
                "verdicts": [{"claim": _FABRICATED_JOB_CLAIM, "verdict": "fabricated"}],
                "reason": "no record basis",
            }
        )
        llm = _JudgedLLM(_FABRICATED_JOB_CLAIM, judge_json)
        pager = _RecordingPager()
        client = _make_app(pager=pager, llm=llm, responders=1)
        _consent(client, "sess-judge-fab")
        r = _chat(client, "sess-judge-fab")
        assert r.status_code == 200, r.text
        body = r.json()

        assert llm.judge_calls == 1
        assert body["trace"]["alignment"]["disposition"] == "suppressed"
        assert body["trace"]["alignment"]["judge_adjudication"] == "fabricated"
        assert "refinery" not in body["response"]
        assert len(pager.pages) == 1
        assert pager.pages[0][1] == PAGE_SEVERITY_SEV1
        assert pager.triggers == [PAGE_TRIGGER_UNGROUNDED_LEAK]

    def test_judge_supported_canon_reply_restored_no_page(self):
        """HU-2161 acceptance #1: a truthful canon-heavy reply the corpus does
        not literally contain (the HU-2070 recurrence class) is cleared by the
        judge — original text restored, disposition passes, nothing pages."""
        judge_json = json.dumps(
            {
                "verdicts": [{"claim": _SUPPORTED_CANON_CLAIM, "verdict": "supported"}],
                "reason": "consistent with the persona record",
            }
        )
        llm = _JudgedLLM(_SUPPORTED_CANON_CLAIM, judge_json)
        pager = _RecordingPager()
        client = _make_app(pager=pager, llm=llm, responders=1)
        _consent(client, "sess-judge-ok")
        r = _chat(client, "sess-judge-ok")
        assert r.status_code == 200, r.text
        body = r.json()

        assert llm.judge_calls == 1
        align = body["trace"]["alignment"]
        assert align["disposition"] == "passed"
        assert align["judge_adjudication"] == "supported"
        assert align["ungrounded_claim_count"] == 0
        # The original in-voice reply was restored, not the canned fallback.
        assert "statistical analysis" in body["response"]
        assert pager.pages == []

    def test_clean_persona_turn_does_not_page(self):
        """A persona turn with no un-grounded claims never pages (no false alarm)."""
        clean_llm = FakeLLMClient(
            response="I remember those warm afternoons by the water.",
            persona_name="Chandler",
        )
        pager = _RecordingPager()
        client = _make_app(pager=pager, llm=clean_llm, responders=1)
        _consent(client, "sess-clean")
        _chat(client, "sess-clean")
        assert pager.pages == []


# --- Trigger #4: consent bypass -------------------------------------------


class TestConsentBypassPage:
    def test_post_hoc_missing_consent_pages_sev1(self):
        """A consent-bypass (gate passed but post-hoc check fails) → Sev-1 page.

        The G6 gate check returns True (consent recorded), but the defensive
        post-hoc re-check right before the reply leaves finds consent missing
        — the §3 Sev-1 (C) bypass. Pages immediately; the turn still returns.
        """
        gate = _BypassConsentGate()
        pager = _RecordingPager()
        client = _make_app(
            pager=pager, consent_gate=gate, responders=1,
            llm=FakeLLMClient(persona_name="Chandler"),
        )
        _consent(client, "sess-bypass")
        r = _chat(client, "sess-bypass")
        assert r.status_code == 200, r.text

        assert len(pager.pages) == 1
        assert pager.pages[0][1] == PAGE_SEVERITY_SEV1
        assert pager.triggers == [PAGE_TRIGGER_CONSENT_BYPASS]

    def test_no_bypass_on_normal_consent_path(self):
        """A normal consented turn (stable gate) never fires the bypass page."""
        pager = _RecordingPager()
        client = _make_app(
            pager=pager, responders=1,
            llm=FakeLLMClient(persona_name="Chandler"),
        )
        _consent(client, "sess-normal")
        _chat(client, "sess-normal")
        # Only the un-grounded-leak trigger could fire on a persona turn; with a
        # clean fake reply it does not, so no pages at all.
        assert pager.pages == []


# --- Failure counter + /metrics surface -----------------------------------


class TestPagingFailuresCounter:
    def _counter_value(self, text: str, trigger: str) -> float:
        pat = re.compile(
            r'^huible_paging_failures_total\{trigger="'
            + re.escape(trigger)
            + r'"\}\s+([0-9.eE+-]+)'
        )
        for line in text.splitlines():
            m = pat.match(line)
            if m:
                return float(m.group(1))
        return 0.0

    def _delta(self, client: TestClient, trigger: str, *, act) -> float:
        """Counter delta for ``trigger`` across ``act`` (robust to global accumulation)."""
        before = self._counter_value(client.get("/metrics").text, trigger)
        act()
        after = self._counter_value(client.get("/metrics").text, trigger)
        return after - before

    def test_failed_real_channel_increments_counter(self):
        """A real-channel page failure increments huible_paging_failures_total."""
        pager = _RecordingPager(failures=2)
        leaky_llm = FakeLLMClient(
            response="I am truly alive and back.", persona_name="Chandler"
        )
        client = _make_app(pager=pager, llm=leaky_llm, responders=1)
        _consent(client, "sess-fail")

        def _act():
            _chat(client, "sess-fail")  # triggers the un-grounded-leak page

        delta = self._delta(client, PAGE_TRIGGER_UNGROUNDED_LEAK, act=_act)
        assert delta >= 2.0

    def test_failed_degraded_net_page_increments_counter(self):
        """The degraded-net trigger's failures surface under their own label."""
        pager = _RecordingPager(failures=1)
        client = _make_app(pager=pager, responders=0)
        _consent(client, "sess-degfail")

        def _act():
            client.post(
                f"/api/v1/chat/{PERSONA_ID}",
                json={
                    "message": "I want to die, I have the pills",
                    "conversation_id": "sess-degfail",
                },
                headers=_auth_headers(),
            )

        delta = self._delta(client, PAGE_TRIGGER_DEGRADED_NET, act=_act)
        assert delta >= 1.0

    def test_successful_page_does_not_increment_counter(self):
        """A page that lands (0 failures) leaves the counter unchanged."""
        pager = _RecordingPager(failures=0)
        leaky_llm = FakeLLMClient(
            response="I am literally present with you.", persona_name="Chandler"
        )
        client = _make_app(pager=pager, llm=leaky_llm, responders=1)
        _consent(client, "sess-ok")

        def _act():
            _chat(client, "sess-ok")  # un-grounded-leak page fires, 0 failures

        delta = self._delta(client, PAGE_TRIGGER_UNGROUNDED_LEAK, act=_act)
        assert delta == 0.0


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
