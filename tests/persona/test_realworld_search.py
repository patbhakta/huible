"""HU-2828: real-world search tool — lanes, profile, query, SearXNG client.

Founder spec (2026-09-11): external lookups route through per-persona
permissioned lanes derived from dialog evidence; the search backend is
self-hosted SearXNG's JSON API (vendor-replaceable behind
:func:`searxng_search`); in-world knowledge stays vault-only.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from huible.persona.realworld import (
    PERSONA_LOCATION_LABEL_KEY,
    PERSONA_SEARCH_LANES_KEY,
    PersonaKnowledgeProfile,
    SearchToolError,
    build_search_query,
    derive_search_lanes,
    is_academic_question,
    is_real_world_question,
    realworld_grounding_text,
    render_realworld_block,
    searxng_search,
)

BASE = "http://searx.test"


# --- Lane classification --------------------------------------------------------


def test_rent_probe_fires_current_reality() -> None:
    assert is_real_world_question("How much is rent where you live?")
    assert is_real_world_question("What's the rent like in your neighborhood?")
    assert is_real_world_question("How much do you pay for your place?")


def test_local_and_current_shapes_fire() -> None:
    assert is_real_world_question("Where do you live?")
    assert is_real_world_question("Who won the game last night?")
    assert is_real_world_question("What's the weather like?")


def test_mayans_probe_does_not_fire_current_reality() -> None:
    # The out-of-world trivia probe must NOT touch the search tool for a
    # current-reality-only persona — honest ignorance is structural.
    assert not is_real_world_question("Tell me about the Mayans")
    assert not is_real_world_question("Do you know anything about the Mayans?")


def test_mayans_probe_classifies_academic() -> None:
    assert is_academic_question("Tell me about the Mayans")
    assert is_academic_question("How does a black hole work?")
    assert is_academic_question("What was the Renaissance?")


def test_empty_and_persona_turns_never_fire() -> None:
    assert not is_real_world_question("")
    assert not is_real_world_question("Do you like your job?")
    assert not is_academic_question("")
    assert not is_academic_question("Remember when I said that?")


# --- Persona-scoped knowledge profile --------------------------------------------


def test_profile_fail_closed_without_metadata() -> None:
    assert PersonaKnowledgeProfile.from_metadata(None) == PersonaKnowledgeProfile()
    assert PersonaKnowledgeProfile.from_metadata({}) == PersonaKnowledgeProfile()
    assert PersonaKnowledgeProfile.from_metadata(
        {PERSONA_SEARCH_LANES_KEY: "current_reality"}  # mistyped
    ) == PersonaKnowledgeProfile()


def test_profile_from_metadata_only_explicit_true_enables() -> None:
    profile = PersonaKnowledgeProfile.from_metadata(
        {PERSONA_SEARCH_LANES_KEY: {"current_reality": True, "academic": False}}
    )
    assert profile.current_reality is True
    assert profile.academic is False
    # Truthy-but-not-True does not enable (fail-closed).
    profile = PersonaKnowledgeProfile.from_metadata(
        {PERSONA_SEARCH_LANES_KEY: {"current_reality": "yes"}}
    )
    assert profile.current_reality is False


def test_derive_search_lanes_from_dialog_evidence() -> None:
    chandler_like = (
        "I pay way too much rent for my apartment in the village. "
        "The landlord, the bills, the subway — and the Knicks game last night."
    )
    lanes = derive_search_lanes(chandler_like)
    assert lanes == {"current_reality": True, "academic": False}

    ross_like = (
        "As a paleontologist at the museum, I gave a lecture on my "
        "excavation dig site for my PhD thesis."
    )
    lanes = derive_search_lanes(ross_like)
    assert lanes == {"current_reality": False, "academic": True}

    assert derive_search_lanes("hello there") == {
        "current_reality": False,
        "academic": False,
    }


# --- Query building ---------------------------------------------------------------


def test_build_search_query_rent_with_location() -> None:
    query = build_search_query(
        "How much is rent where you live?", "Greenwich Village, New York"
    )
    assert query == "rent Greenwich Village, New York"


def test_build_search_query_without_location_falls_back() -> None:
    query = build_search_query("How much is rent where you live?")
    assert "rent" in query
    assert "local area" in query


def test_build_search_query_keeps_non_scaffolded_shapes() -> None:
    assert build_search_query("Who won the game last night?") == (
        "Who won the game last night"
    )


# --- SearXNG client -----------------------------------------------------------------


def _searx_transport(payload: Any) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, int):
            return httpx.Response(payload, text="boom")
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler), requests


async def test_searxng_search_normalizes_hits() -> None:
    payload = {
        "results": [
            {
                "title": "Rent in NYC",
                "url": "https://example.test/rent",
                "content": "Median rent in Greenwich Village is $4,200/month.",
            },
            {"title": "", "content": ""},  # dropped
            "not-a-dict",  # dropped
            {"title": "Second", "url": "", "content": "Another fact."},
        ]
    }
    transport, requests = _searx_transport(payload)
    hits = await searxng_search(
        "rent Greenwich Village", base_url=BASE, transport=transport
    )
    assert [h.title for h in hits] == ["Rent in NYC", "Second"]
    assert "rent+Greenwich+Village" in str(requests[0].url) or (
        requests[0].url.params["q"] == "rent Greenwich Village"
    )
    assert requests[0].url.params["format"] == "json"


async def test_searxng_search_respects_limit() -> None:
    payload = {"results": [{"title": f"t{i}", "content": f"c{i}"} for i in range(10)]}
    transport, _ = _searx_transport(payload)
    hits = await searxng_search("q", base_url=BASE, transport=transport, limit=2)
    assert len(hits) == 2


async def test_searxng_search_errors_are_search_tool_errors() -> None:
    for payload in (httpx.ConnectError("down"), 503, "not-json", [1, 2]):
        transport, _ = _searx_transport(payload)
        with pytest.raises(SearchToolError):
            await searxng_search("q", base_url=BASE, transport=transport)
    with pytest.raises(SearchToolError):
        await searxng_search("  ", base_url=BASE, transport=_searx_transport({})[0])


# --- Rendering + grounding ------------------------------------------------------------


def test_render_realworld_block_and_grounding_text() -> None:
    from huible.persona.realworld import SearchHit

    hits = [
        SearchHit(
            title="Rent in NYC",
            url="https://example.test/rent",
            content="Median rent in Greenwich Village is $4,200/month.",
        )
    ]
    block = render_realworld_block(hits, "Greenwich Village, New York")
    assert "CURRENT-WORLD NOTES" in block
    assert "Greenwich Village" in block
    assert "$4,200/month" in block
    assert render_realworld_block([], "x") == ""

    grounding = realworld_grounding_text(hits)
    assert "Rent in NYC" in grounding
    assert "$4,200/month" in grounding
    assert realworld_grounding_text([]) == ""
