#!/usr/bin/env python3
"""Huible Memory Engine & Conversation Loop Demo.
Loads Pat's CSV memory file (sample_memories.csv) into Huible memory engine,
executes multi-turn conversation with spreading activation retrieval and memory extraction.
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from huible.conversation import (
    HuibleCSVLoader,
    HuibleConversationLoop,
    InMemoryMemoryBackend,
    PersonaConfig,
)


async def main():
    print("=" * 80)
    print("HUIBLE MEMORY ENGINE & CONVERSATION LOOP DEMO")
    print("=" * 80)

    # 1. Initialize Persona and Memory Store
    persona = PersonaConfig(
        id=uuid4(),
        name="Pat",
        display_name="Pat Bhakta",
        voice_instructions="Reflective, helpful, tea lover, software innovator.",
        era_knowledge_boundary="2026-08-06",
    )
    backend = InMemoryMemoryBackend()

    # 2. Locate and Ingest Pat's CSV Data
    csv_path = Path("/root/repos/sandbox/data/sample_memories.csv")
    if not csv_path.exists():
        csv_path = Path("data/sample_memories.csv")

    print(f"\n[1] Ingesting Pat's memory data CSV: {csv_path}")
    nodes = await HuibleCSVLoader.load_csv(csv_path, persona.id, backend)
    print(f" -> Ingested {len(nodes)} memory nodes into Huible Spreading Activation Graph.")

    for i, node in enumerate(nodes, 1):
        print(f"    {i}. [{node.tier.value.upper()}/{node.content_type.value.upper()}] {node.content}")

    # 3. Initialize Conversation Loop
    loop = HuibleConversationLoop(persona, backend)

    # 4. Multi-Turn Interactive Conversation Simulation
    sample_queries = [
        "What kind of tea do you like to drink on Sunday mornings?",
        "What did you mention about your grandfather in Gujarat?",
        "How do you feel about Hermes context loss during long refactorings?",
        "How does Huible's memory engine compare to standard RAG?",
    ]

    print("\n[2] Executing Multi-Turn Conversation Loop with Spreading Activation...")
    print("-" * 80)

    for turn_idx, query in enumerate(sample_queries, 1):
        print(f"\n--- Turn {turn_idx} ---")
        print(f"User: {query}")

        res = await loop.turn(query, speaker_name="Pat")

        print(f"\nActivated Memories ({len(res['activated_memories'])} retrieved):")
        for mem in res["activated_memories"]:
            print(f"  - Score {mem['activation']}: [{mem['type'].upper()}] {mem['content']}")

        print(f"\nPersona Response:\n  {res['response']}")
        print("-" * 80)

    print("\n[3] Conversation Summary")
    print(f" Total Turns Executed: {len(loop.turn_history) // 2}")
    print(f" Total Memory Graph Nodes: {len(backend.memories)}")
    print(f" Total Memory Graph Edges: {len(backend.edges)}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
