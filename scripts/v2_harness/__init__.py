"""HU-2706 — v2 internal validation harness (HU-2309 plan §1.8, v1.9).

Four permanent artifacts that ship WITH persona v2:

- H1  M-0 calibration replay    — the boss's collected M-0 violations become
                                  permanent regression probes on the E0 replay
                                  rig; gate: v2 reproduces NONE of them.
- H2  AI-tell probe suite       — automated adversarial turn classes with
                                  per-class measured result tables (scored
                                  against measured corpus baselines).
- H3  Vault-grounding ledger    — per-reply ledger over the M-0R-A trace-score
                                  passthrough: memory IDs, retrieval scores,
                                  era-gate tool calls per reply.
- H4  Five-Friends blind kit    — packaged ready-to-run blind test. Runs only
                                  when the boss chooses; never self-graded.

Measure-first: every artifact records measured numbers. The ONLY pre-set
binary gate is the boss-mandated one — v2 must not reproduce any collected
M-0 violation (H1).
"""
