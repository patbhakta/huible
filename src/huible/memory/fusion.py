"""Reciprocal Rank Fusion (RRF) over ranked retrieval lanes — HU-2309 W2.

Pure ranking combiner: given the per-lane ranked document lists (best first),
score every document by the RRF formula and return the fused ranking::

    rrf(d) = sum over lanes containing d of 1 / (k + rank(d, lane))

with 1-based ranks and the standard ``k=60`` constant (Cormack et al. 2009;
same constant the BEAM v4 Arm C pattern and docs/MNEMOSYNE_DESIGN.md §Hybrid
Search use). RRF needs only ordinal information, so lanes with incomparable
score scales (cosine similarity vs ``ts_rank`` lexical rank) fuse without any
score normalization.

Mirrors the design W2 row: "Postgres FTS over memories; RRF-fuse with the
vector lane" (docs/design/HU-2309-persona-vault-design.md §1.7.2). The
combiner is a pure function of its inputs — no I/O, no clock, no RNG — so it
is deterministic and trivially unit-testable.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

#: Default RRF constant. Higher k flattens the per-rank contribution, making
#: the fusion less dominated by the top of each lane. 60 is the standard
#: value from the literature (Cormack et al. 2009) and the BEAM v4 Arm C
#: pattern this module mirrors.
DEFAULT_RRF_K = 60


def rrf_fuse(
    rankings: Sequence[Sequence[UUID]],
    k: int = DEFAULT_RRF_K,
) -> list[tuple[UUID, float]]:
    """Fuse ranked document lists into one RRF-ranked list.

    Args:
        rankings: One ranked list per lane (best document first). Lanes may
            overlap arbitrarily; duplicates *within* a lane are treated as a
            single appearance at the document's best rank.
        k: RRF rank-constant (``1 / (k + rank)`` per lane contribution).

    Returns:
        ``[(doc_id, rrf_score), ...]`` sorted by RRF score descending. Score
        ties break by first appearance across the fused lanes (lane order,
        then position), so the output is deterministic for identical inputs.
        Documents absent from every lane do not appear.

    Raises:
        ValueError: If ``k`` is not a positive integer.
    """
    if k < 1:
        raise ValueError(f"RRF k must be >= 1, got {k}")

    scores: dict[UUID, float] = {}
    first_seen: dict[UUID, int] = {}
    order = 0
    for ranking in rankings:
        seen_in_lane: set[UUID] = set()
        for position, doc_id in enumerate(ranking, start=1):
            # Dedupe within a lane at the best (lowest) rank.
            if doc_id in seen_in_lane:
                continue
            seen_in_lane.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position)
            if doc_id not in first_seen:
                first_seen[doc_id] = order
                order += 1

    return sorted(
        scores.items(),
        key=lambda item: (-item[1], first_seen[item[0]]),
    )
