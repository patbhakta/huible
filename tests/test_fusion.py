from __future__ import annotations

from uuid import uuid4

import pytest

from huible.memory.fusion import DEFAULT_RRF_K, rrf_fuse


def _ids(n: int) -> list:
    return [uuid4() for _ in range(n)]


class TestRRFFuse:
    def test_empty_input_yields_empty_fusion(self) -> None:
        assert rrf_fuse([]) == []
        assert rrf_fuse([[]]) == []
        assert rrf_fuse([[], []]) == []

    def test_single_lane_preserves_order_with_rrf_scores(self) -> None:
        a, b = _ids(2)
        fused = rrf_fuse([[a, b]], k=60)
        assert [doc for doc, _ in fused] == [a, b]
        # 1-based ranks: 1/(k+1), 1/(k+2)
        assert fused[0][1] == pytest.approx(1 / 61)
        assert fused[1][1] == pytest.approx(1 / 62)

    def test_known_value_two_lanes_overlap(self) -> None:
        a, b, c = _ids(3)
        fused = rrf_fuse([[a, b], [c, a]], k=60)
        scores = dict(fused)
        # a: rank 1 in lane 1 + rank 2 in lane 2
        assert scores[a] == pytest.approx(1 / 61 + 1 / 62)
        # b: rank 2 in lane 1 only; c: rank 1 in lane 2 only — exact tie
        assert scores[b] == pytest.approx(1 / 62)
        assert scores[c] == pytest.approx(1 / 61)
        # Overlap outranks single-lane docs; c beats b (1/61 > 1/62)
        assert [doc for doc, _ in fused] == [a, c, b]

    def test_tie_breaks_deterministically_by_first_appearance(self) -> None:
        b, c, d = _ids(3)
        # b and c each appear once at rank 1 of their respective lane (exact
        # tie at 1/(k+1)); d appears at rank 3 in both lanes (1/63+1/63).
        fused = rrf_fuse([[b, d], [c, d]], k=60)
        scores = dict(fused)
        assert scores[b] == pytest.approx(scores[c])
        assert scores[d] > scores[b]
        # d first (both lanes); then the exact tie between b/c, broken
        # deterministically by lane order (b's lane comes first).
        assert fused[0][0] == d
        assert fused[1] == (b, scores[b])
        assert fused[2] == (c, scores[c])

    def test_duplicate_within_lane_counts_once_at_best_rank(self) -> None:
        a, b = _ids(2)
        fused = rrf_fuse([[a, b, a]], k=60)
        assert len(fused) == 2
        assert dict(fused)[a] == pytest.approx(1 / 61)

    def test_smaller_k_sharpens_top_ranks(self) -> None:
        a, b, c = _ids(3)
        # With k=1 the top rank's contribution dwarfs lower ranks.
        fused_low_k = rrf_fuse([[a], [b, c]], k=1)
        scores = dict(fused_low_k)
        assert scores[a] == pytest.approx(1 / 2)
        assert scores[c] == pytest.approx(1 / 3)
        assert fused_low_k[0][0] == a

    def test_default_k_is_standard_constant(self) -> None:
        a = _ids(1)[0]
        fused = rrf_fuse([[a]])
        assert DEFAULT_RRF_K == 60
        assert fused[0][1] == pytest.approx(1 / (DEFAULT_RRF_K + 1))

    def test_invalid_k_raises(self) -> None:
        a = _ids(1)[0]
        with pytest.raises(ValueError, match="k must be >= 1"):
            rrf_fuse([[a]], k=0)
        with pytest.raises(ValueError, match="k must be >= 1"):
            rrf_fuse([[a]], k=-5)

    def test_determinism_identical_inputs_identical_output(self) -> None:
        lanes = [_ids(5) for _ in range(2)]
        assert rrf_fuse(lanes, k=60) == rrf_fuse(lanes, k=60)
