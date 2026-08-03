from __future__ import annotations

import hashlib
import math
import random
from datetime import date
from uuid import uuid4

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    EdgeType,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SourceType,
)

_PERSONAS = {
    "bob": "Robert Mitchell",
    "alice": "Alice Mitchell",
    "carol": "Carol Henderson",
    "dave": "Dave Chen",
    "eve": "Eve Ramirez",
}

_TOPICS = [
    "fishing",
    "navy",
    "engineering",
    "family_gathering",
    "christmas",
    "cooking",
    "music",
    "travel",
    "gardening",
    "sports",
    "work_project",
    "school_days",
    "health",
    "home_repair",
    "pets",
]

_CONTENT_TEMPLATES: dict[ContentType, list[str]] = {
    ContentType.NARRATIVE: [
        "{persona} remembers {topic} with {participant}. It was {adj} and {adj2}.",
        "That time {persona} and {participant} went {topic}-related — {adj} experience.",
        "{persona} often told the story about {topic} when {participant} was there. Always {adj}.",
        "Back in {era}, {persona} used to {verb} for {topic}. {participant} remembers it well.",
        "Every {season}, {persona} would {verb} related to {topic}. Those were {adj} days.",
    ],
    ContentType.FACT: [
        "{persona} worked as a {job} at {company} from {year} to {year2}.",
        "{persona} graduated from {school} in {year} with a degree in {field}.",
        "{persona} served in the Navy as a {rank} from {year} to {year2}.",
        "{persona} was born on {month} {day}, {year} in {city}, {state}.",
        "{persona} married {participant} in {month} {year} at {venue}.",
    ],
    ContentType.SENSORY: [
        "The smell of {food} always reminded {persona} of {topic} days.",
        "{persona} could hear {sound} — that was the {topic} season sound.",
        "Cold {beverage} and {topic}. {persona} loved that {adj} combination.",
        "The texture of {material} — {persona} always associated it with {topic}.",
        "Sunlight through the {object} during {topic} — {adj} warmth.",
    ],
    ContentType.RELATIONSHIP: [
        "{persona} and {participant} had a {adj} bond centered around {topic}.",
        "{participant} was {persona}'s closest {role} when it came to {topic}.",
        "{persona} trusted {participant} deeply, especially about {topic} matters.",
        "{persona} and {participant}'s relationship was defined by {adj} {topic} moments.",
        "{persona} considered {participant} family because of shared {topic} history.",
    ],
    ContentType.PREFERENCE: [
        "{persona} always preferred {food} when {topic} came up.",
        "{persona} favored {adj} weather for {topic} activities.",
        "{persona} would always choose {option} over {option2} for {topic}.",
        "When it came to {topic}, {persona}'s go-to was {food}.",
        "{persona} never liked {activity} but loved {topic}-related {activity2}.",
    ],
}

_FACTS = [
    ("worked as a mechanical engineer at Lockheed Martin", "mechanical engineer",
     "Lockheed Martin"),
    ("served in the US Navy as a petty officer", "petty officer", "US Navy"),
    ("graduated from UT Austin in 1974", "bachelor", "UT Austin"),
    ("married Alice in June 1978", "spouse", "Alice"),
    ("had two children, Michael and Sarah", "father", "family"),
    ("lived in Austin, Texas from 1976 to 2021", "resident", "Austin"),
    ("retired from Lockheed in 2012", "retired", "Lockheed"),
    ("enjoyed bass fishing on Lake Travis", "fisherman", "Lake Travis"),
    ("played guitar in a country band in the 80s", "musician", "country band"),
    ("volunteered at the Veterans Hospital", "volunteer", "VA Hospital"),
]

_ADJS = [
    "wonderful", "memorable", "special", "unforgettable", "warm", "joyful",
    "peaceful", "exciting", "heartwarming", "nostalgic", "beautiful", "fun",
    "emotional", "quiet", "loud", "happy", "sad", "bittersweet", "simple", "perfect",
]

_VERBS = [
    "go", "build", "create", "enjoy", "share", "remember", "celebrate", "work",
    "play", "cook", "fish", "sing", "dance", "travel", "garden", "fix", "teach",
    "learn", "read", "write", "drive", "walk", "run", "swim", "hike",
]

_FILLERS: dict[str, list[str]] = {
    "food": ["barbecue", "pecan pie", "fried chicken", "tamales", "chili", "cornbread"],
    "sound": ["country music", "crickets", "rain on a tin roof", "a guitar", "laughing"],
    "beverage": ["sweet tea", "coffee", "Lone Star beer", "lemonade"],
    "material": ["old leather", "denim", "rough cedar", "warm wool"],
    "object": ["kitchen window", "porch screen", "car windshield"],
    "job": ["mechanical engineer", "project manager", "consultant"],
    "company": ["Lockheed Martin", "Boeing", "Dell", "3M"],
    "school": ["UT Austin", "Texas A&M", "Rice University"],
    "field": ["mechanical engineering", "aerospace", "computer science"],
    "rank": ["petty officer second class", "chief petty officer", "ensign"],
    "city": ["Austin", "Houston", "San Antonio", "Dallas"],
    "state": ["Texas", "California", "Florida"],
    "venue": ["St. Mary's Church", "the courthouse", "a park", "the backyard"],
    "month": ["January", "March", "June", "September", "November"],
    "season": ["summer", "fall", "winter", "spring"],
    "era": ["the 70s", "the 80s", "the 90s", "the early 2000s"],
    "role": ["friend", "brother-in-law", "colleague", "neighbor", "fishing buddy"],
    "option": ["the old way", "the simple approach", "the traditional method"],
    "option2": ["the modern way", "the complicated route", "the new approach"],
    "activity": ["running", "fancy restaurants", "loud parties"],
    "activity2": ["quiet fishing", "backyard cookouts", "garage projects"],
}


def _pick(lst: list[str], rng: random.Random) -> str:
    return rng.choice(lst)


def _fill(template: str, rng: random.Random) -> str:
    text = template
    for key, values in _FILLERS.items():
        while f"{{{key}}}" in text:
            text = text.replace(f"{{{key}}}", _pick(values, rng), 1)
    text = text.replace("{adj}", _pick(_ADJS, rng))
    text = text.replace("{adj2}", _pick(_ADJS, rng))
    text = text.replace("{verb}", _pick(_VERBS, rng))
    text = text.replace("{persona}", "Bob")
    text = text.replace("{participant}", _pick(["Alice", "Carol", "Dave"], rng))
    text = text.replace("{topic}", _pick(_TOPICS, rng))
    text = text.replace("{year}", str(rng.randint(1960, 2020)))
    text = text.replace("{year2}", str(rng.randint(1970, 2021)))
    text = text.replace("{day}", str(rng.randint(1, 28)))
    return text


def _hash_to_embedding(text: str, dim: int, seed: int = 0) -> list[float]:
    h = hashlib.sha512(f"{seed}:{text}".encode()).digest()
    result: list[float] = []
    for i in range(dim):
        chunk = h[i % len(h) : i % len(h) + 4]
        val = int.from_bytes(chunk, "big") / 0xFFFFFFFF
        result.append(val * 2.0 - 1.0)
    norm = math.sqrt(sum(x * x for x in result))
    if norm > 0:
        result = [x / norm for x in result]
    return result


def _hash_to_topic_embedding(
    topic: str, dim: int, rng: random.Random, seed: int = 0
) -> list[float]:
    base = _hash_to_embedding(topic, dim, seed)
    noise: list[float] = []
    for _ in range(dim):
        noise.append(rng.gauss(0, 0.15))
    combined = [b + n for b, n in zip(base, noise, strict=False)]
    norm = math.sqrt(sum(x * x for x in combined))
    if norm > 0:
        combined = [x / norm for x in combined]
    return combined


class SyntheticCorpus:
    def __init__(
        self,
        n_memories: int = 1050,
        n_edges: int = 3000,
        seed: int = 42,
    ) -> None:
        self.n_memories = n_memories
        self.n_edges = n_edges
        self.seed = seed
        self.rng = random.Random(seed)
        self.persona_id = uuid4()
        self.memories: list[MemoryNode] = []
        self.edges: list[MemoryEdge] = []

    def generate(self) -> tuple[list[MemoryNode], list[MemoryEdge]]:
        self._generate_memories()
        self._generate_edges()
        return self.memories, self.edges

    def _generate_memories(self) -> None:
        tiers = list(MemoryTier)
        scopes = list(DisclosureScope)
        content_types = list(ContentType)

        tier_weights = [0.10, 0.15, 0.55, 0.20]
        scope_weights = [0.15, 0.35, 0.30, 0.20]

        for i in range(self.n_memories):
            tier = self.rng.choices(tiers, weights=tier_weights, k=1)[0]
            scope = self.rng.choices(scopes, weights=scope_weights, k=1)[0]
            ct = self.rng.choice(content_types)

            if ct == ContentType.FACT and i < len(_FACTS):
                content = f"Bob {_FACTS[i][0]}."
                source_type = (
                    SourceType.CANONICAL_SEED
                    if tier == MemoryTier.CANONICAL
                    else SourceType.EXTRACTION
                )
            else:
                templates = _CONTENT_TEMPLATES[ct]
                content = _fill(self.rng.choice(templates), self.rng)
                source_type = SourceType.EXTRACTION

            topic = self.rng.choice(_TOPICS)

            content_emb = _hash_to_topic_embedding(content, 1536, self.rng)
            sensory_emb = _hash_to_topic_embedding(f"sensory:{content}", 1536, self.rng)
            affect_emb = _hash_to_topic_embedding(f"affect:{content}", 512, self.rng)

            mem_date = date(
                self.rng.randint(1950, 2021),
                self.rng.randint(1, 12),
                self.rng.randint(1, 28),
            )

            node = MemoryNode(
                id=uuid4(),
                persona_id=self.persona_id,
                tier=tier,
                content=content,
                content_type=ct,
                embedding_content=content_emb,
                embedding_sensory=sensory_emb,
                embedding_affect=affect_emb,
                memory_date=mem_date,
                source_type=source_type,
                disclosure_scope=scope,
                metadata={"topic": topic},
            )
            self.memories.append(node)

    def _generate_edges(self) -> None:
        topic_groups: dict[str, list[MemoryNode]] = {}
        for mem in self.memories:
            topic = mem.metadata.get("topic", "")
            topic_groups.setdefault(topic, []).append(mem)

        all_nodes = self.memories

        for _ in range(self.n_edges * 3):
            if len(self.edges) >= self.n_edges:
                break

            method = self.rng.random()
            n1, n2 = self.rng.sample(all_nodes, 2)

            if method < 0.35 and len(topic_groups) > 1:
                topic = self.rng.choice(list(topic_groups.keys()))
                group = topic_groups[topic]
                if len(group) >= 2:
                    n1, n2 = self.rng.sample(group, 2)
                    edge_type = EdgeType.THEMATIC
                    weight = self.rng.uniform(0.5, 1.0)
                else:
                    edge_type = EdgeType.THEMATIC
                    weight = self.rng.uniform(0.3, 0.7)
            elif method < 0.60:
                date1 = n1.memory_date or date(2000, 1, 1)
                date2 = n2.memory_date or date(2000, 1, 1)
                days_apart = abs((date1 - date2).days)
                if days_apart <= 365:
                    weight = max(0.2, 1.0 - days_apart / 365.0)
                    edge_type = EdgeType.TEMPORAL_PROXIMITY
                else:
                    edge_type = EdgeType.THEMATIC
                    weight = self.rng.uniform(0.2, 0.6)
            elif method < 0.80:
                edge_type = EdgeType.SHARED_PARTICIPANT
                weight = self.rng.uniform(0.5, 0.9)
            else:
                edge_type = self.rng.choice([
                    EdgeType.CAUSAL,
                    EdgeType.ELABORATION,
                    EdgeType.CONTRADICTION,
                ])
                weight = self.rng.uniform(0.3, 0.8)

            edge = MemoryEdge(
                id=uuid4(),
                source_id=n1.id,
                target_id=n2.id,
                edge_type=edge_type,
                weight=weight,
            )
            self.edges.append(edge)
