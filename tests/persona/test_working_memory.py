"""W4 working-memory tests (HU-2309 v1.8 §1.7.2 / M-0R-B).

Covers:

- :class:`huible.persona.working_memory.TencentWorkingMemory` — Arm A recall
  parsing (``v4-arm-a`` payload, v3 fallback, gateway error envelope), turn
  capture, and the failure doctrine: every transport / protocol failure
  degrades to empty / False without raising.
- :func:`working_memory_session_key` — ``huible-`` namespacing + per-persona
  scoping (2026-08-16 contamination doctrine).
- :class:`ContextBuilder` W4 render — the working-memory block lands in its
  own WORKING MEMORY section ahead of CONVERSATION HISTORY, and an empty
  block leaves the prompt byte-identical to the pre-W4 shape.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar
from uuid import uuid4

import pytest

from huible.persona.context import (
    ContextBuilder,
    ConversationTurn,
    PersonaConfig,
    RelationshipTier,
)
from huible.persona.working_memory import (
    ARM_A_STRATEGY,
    NullWorkingMemory,
    TencentWorkingMemory,
    WorkingMemoryRecall,
    working_memory_session_key,
)

# ---------------------------------------------------------------------------
# Session-key isolation
# ---------------------------------------------------------------------------


def test_session_key_namespaced_and_scoped() -> None:
    pid = uuid4()
    key = working_memory_session_key(pid, "demo-abc")
    assert key.startswith("huible-")
    assert str(pid) in key
    assert "demo-abc" in key


def test_session_key_never_shared_across_personas_or_conversations() -> None:
    key_a = working_memory_session_key(uuid4(), "conv-1")
    key_b = working_memory_session_key(uuid4(), "conv-1")
    key_c = working_memory_session_key(uuid4(), "conv-2")
    assert len({key_a, key_b, key_c}) == 3


def test_session_key_disjoint_from_beam_and_hermes_scopes() -> None:
    key = working_memory_session_key(uuid4(), "x")
    assert not key.startswith("beam-")
    assert not key.startswith("agt-beam")


# ---------------------------------------------------------------------------
# Local gateway stub (stdlib HTTP server) + unreachable-gateway fixtures
# ---------------------------------------------------------------------------


class _StubGateway(BaseHTTPRequestHandler):
    """Records /recall and /capture posts; replies per the configured script."""

    recall_body = json.dumps(
        {
            "context": "",
            "prepend_context": "DIGEST-MARKER turn 1: hey who r u?",
            "strategy": ARM_A_STRATEGY,
            "memory_count": 2,
            "code": 0,
            "message": "ok",
        }
    ).encode()
    capture_code = 0
    requests: ClassVar[list[tuple[str, dict[str, Any]]]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append((self.path, body))
        if self.path == "/recall":
            payload, code = type(self).recall_body, 200
        else:
            payload = json.dumps(
                {"l0_recorded": 2, "scheduler_notified": True, "code": type(self).capture_code}
            ).encode()
            code = 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:  # silence
        return


@pytest.fixture()
def gateway_url() -> str:
    server = HTTPServer(("127.0.0.1", 0), _StubGateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _StubGateway.requests = []
    _StubGateway.recall_body = json.dumps(
        {
            "context": "",
            "prepend_context": "DIGEST-MARKER turn 1: hey who r u?",
            "strategy": ARM_A_STRATEGY,
            "memory_count": 2,
            "code": 0,
            "message": "ok",
        }
    ).encode()
    _StubGateway.capture_code = 0
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _client(url: str) -> TencentWorkingMemory:
    return TencentWorkingMemory(url, timeout_s=5.0)


# ---------------------------------------------------------------------------
# Recall / capture happy paths
# ---------------------------------------------------------------------------


async def test_recall_parses_arm_a_payload(gateway_url: str) -> None:
    recall = await _client(gateway_url).recall("huible-s", "what did I say first?")
    assert recall.context.startswith("DIGEST-MARKER")
    assert recall.strategy == ARM_A_STRATEGY
    assert recall.chars == len(recall.context)


async def test_recall_posts_query_and_namespaced_session(gateway_url: str) -> None:
    await _client(gateway_url).recall("huible-s", "the question")
    path, body = _StubGateway.requests[-1]
    assert path == "/recall"
    assert body["session_key"] == "huible-s"
    assert body["query"] == "the question"


async def test_capture_posts_user_assistant_pair(gateway_url: str) -> None:
    ok = await _client(gateway_url).capture("huible-s", "user text", "persona text")
    assert ok is True
    path, body = _StubGateway.requests[-1]
    assert path == "/capture"
    assert body["user_content"] == "user text"
    assert body["assistant_content"] == "persona text"
    assert body["session_key"] == "huible-s"


async def test_capture_truncates_to_gateway_limit(gateway_url: str) -> None:
    big = "x" * 9000
    await _client(gateway_url).capture("huible-s", big, big)
    _, body = _StubGateway.requests[-1]
    assert len(body["user_content"]) <= 8000
    assert len(body["assistant_content"]) <= 8000


async def test_recall_v3_fallback_still_served_as_excerpts(gateway_url: str) -> None:
    _StubGateway.recall_body = json.dumps(
        {"context": "sys-context", "strategy": "hybrid", "code": 0, "message": "ok"}
    ).encode()
    recall = await _client(gateway_url).recall("huible-s", "q")
    # v3 fallback has no prepend_context (no Arm A digest); context stays empty.
    assert recall.context == ""
    assert recall.strategy == "hybrid"


# ---------------------------------------------------------------------------
# Failure doctrine: degrade, never raise
# ---------------------------------------------------------------------------


async def test_recall_gateway_error_envelope_is_empty(gateway_url: str) -> None:
    _StubGateway.recall_body = json.dumps(
        {"code": 20001, "message": "timeout", "retryable": True}
    ).encode()
    recall = await _client(gateway_url).recall("huible-s", "q")
    assert recall.context == ""
    assert recall.chars == 0


async def test_recall_unreachable_gateway_is_empty() -> None:
    # Closed port: connection refused -> URLError -> empty recall, no raise.
    client = TencentWorkingMemory("http://127.0.0.1:1", timeout_s=1.0)
    recall = await client.recall("huible-s", "q")
    assert recall == WorkingMemoryRecall.empty()


async def test_recall_non_json_body_is_empty(gateway_url: str) -> None:
    _StubGateway.recall_body = b"<html>not json</html>"
    recall = await _client(gateway_url).recall("huible-s", "q")
    assert recall.context == ""


async def test_recall_empty_inputs_short_circuit(gateway_url: str) -> None:
    client = _client(gateway_url)
    assert (await client.recall("", "q")).context == ""
    assert (await client.recall("huible-s", "")).context == ""
    assert _StubGateway.requests == []


async def test_capture_gateway_error_is_false(gateway_url: str) -> None:
    _StubGateway.capture_code = 500
    ok = await _client(gateway_url).capture("huible-s", "u", "a")
    assert ok is False


async def test_capture_unreachable_gateway_is_false() -> None:
    client = TencentWorkingMemory("http://127.0.0.1:1", timeout_s=1.0)
    assert await client.capture("huible-s", "u", "a") is False


async def test_capture_empty_inputs_short_circuit(gateway_url: str) -> None:
    client = _client(gateway_url)
    assert await client.capture("", "u", "a") is False
    assert await client.capture("huible-s", "", "a") is False
    assert await client.capture("huible-s", "u", "") is False
    assert _StubGateway.requests == []


async def test_null_lane_is_inert() -> None:
    lane = NullWorkingMemory()
    assert (await lane.recall("k", "q")) == WorkingMemoryRecall.empty()
    assert await lane.capture("k", "u", "a") is False


# ---------------------------------------------------------------------------
# Context-builder W4 render
# ---------------------------------------------------------------------------


def _persona() -> PersonaConfig:
    return PersonaConfig(id=uuid4(), name="Chandler Bing")


def test_working_memory_renders_before_history() -> None:
    ctx = ContextBuilder().filter_and_render(
        [],
        _persona(),
        RelationshipTier.FAMILY,
        conversation_history=[],
        current_message="what was the first thing I said to you?",
        working_memory="DIGEST-MARKER turn 1: hey who r u?",
    )
    rendered = ctx.render()
    wm_pos = rendered.index("WORKING MEMORY")
    hist_pos = rendered.index("CONVERSATION HISTORY:")
    cur_pos = rendered.index("CURRENT MESSAGE:")
    assert wm_pos < hist_pos < cur_pos
    assert "DIGEST-MARKER" in rendered
    # Evidence separation: the block is prompt surface, not vault memory.
    assert ctx.included_memories == []
    assert ctx.working_memory.startswith("DIGEST-MARKER")


def test_empty_working_memory_renders_pre_w4_shape() -> None:
    builder = ContextBuilder()
    baseline = builder.filter_and_render([], _persona(), RelationshipTier.FAMILY)
    armed = builder.filter_and_render(
        [], _persona(), RelationshipTier.FAMILY, working_memory=""
    )
    assert armed.render() == baseline.render()
    assert "WORKING MEMORY" not in baseline.render()


def test_first_utterance_survives_beyond_the_window() -> None:
    # The RC-3 eviction shape: the probe at turn 15 (29 stored turns) must
    # still see session turn 1 ("hey who r u?") verbatim in the history —
    # bounded head keeps the unsettled block fully covered.
    turns = [
        ConversationTurn(speaker="user" if i % 2 == 0 else "persona", content=f"m{i}")
        for i in range(29)
    ]
    turns[0] = ConversationTurn(speaker="user", content="hey who r u?")
    ctx = ContextBuilder().filter_and_render(
        [], _persona(), RelationshipTier.FAMILY, conversation_history=turns
    )
    assert "user: hey who r u?" in ctx.conversation_history
