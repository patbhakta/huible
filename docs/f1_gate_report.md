# F1 Gate Report — Phase 1 Exit Gate

**Overall: PASS**
**Timestamp:** 2026-07-30T05:52 UTC
**Total F1 tests:** 30 criterion tests + 7 benchmark tests = **37 passed, 0 failed**

## Criteria

| Criterion | Status | Passed | Failed | Skipped | Time |
|-----------|--------|--------|--------|---------|------|
| F1.1 Indexing (multi-vector embeddings, 1000+ memories) | **PASS** | 9 | 0 | 0 | ~4s |
| F1.2 Spreading Activation (edge traversal, not just cosine) | **PASS** | 5 | 0 | 0 | ~5s |
| F1.3 Feedback Loop Suppression (same query → different results) | **PASS** | 4 | 0 | 0 | ~6s |
| F1.4 Disclosure Tier Scoping (private excluded for lower tiers) | **PASS** | 6 | 0 | 0 | ~6s |
| F1.5 Cross-Topic Motif Escalation (motif-boosted activation) | **PASS** | 6 | 0 | 0 | ~5s |

## Performance Benchmarks

| Benchmark | Status | Detail |
|-----------|--------|--------|
| Spread activation (100K nodes, 500K edges) | **PASS** | < 5s |
| Suppression (100K activation map) | **PASS** | < 50ms |
| Motif escalation (100K activation map) | **PASS** | < 500ms |
| Disclosure filter (100K activation map) | **PASS** | < 500ms |
| In-memory retrieval (5K corpus) | **PASS** | < 2s |
| Indexing throughput | **PASS** | > 100/s |

## Deliverables

- `tests/f1/` — pytest-based F1 test harness
- `tests/f1/corpus.py` — Synthetic memory corpus generator (1050 memories, 3000 edges)
- `tests/f1/conftest.py` — CosineFakeBackend with actual cosine similarity + shared fixtures
- `tests/f1/test_f1_1_indexing.py` — F1.1: multi-vector embedding indexing
- `tests/f1/test_f1_2_spreading_activation.py` — F1.2: graph-traversal retrieval
- `tests/f1/test_f1_3_feedback_suppression.py` — F1.3: feedback loop suppression
- `tests/f1/test_f1_4_disclosure_scoping.py` — F1.4: disclosure tier filtering
- `tests/f1/test_f1_5_motif_escalation.py` — F1.5: cross-topic motif escalation
- `tests/f1/test_f1_benchmarks.py` — Performance benchmarks
- `tests/f1/report.py` — F1 gate report generator (markdown + JSON)

## Notes

- All tests use a CosineFakeBackend (in-memory) with real cosine similarity for search
- The 100K-corpus production benchmark requires PostgreSQL + pgvector HNSW indexes; algorithm-level benchmarks at 100K scale pass with in-memory adjacency lists
- Zero regressions to existing test suite (139/139 existing tests unchanged; 5 pre-existing failures in test_pipeline.py from BHAA-1335)
