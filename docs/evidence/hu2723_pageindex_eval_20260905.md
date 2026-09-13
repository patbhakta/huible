# HU-2723 — PageIndex Flash eval: groundwork, reframe, retrieval head-to-head (2026-09-05)

Founder field notes (Pat, 2026-09-05) reframed the evaluation: PageIndex
Flash is the **reference baseline to beat**, not an adoption candidate.
Our proprietary recipe (TencentDB + vaults + in-house retrieval) is the
system under test. Success = match-or-beat PageIndex retrieval quality
at a fraction of the cost. Multi-page-span questions are a first-class
slice (PageIndex's documented soft spot), cost is first-class.

## What ran this heartbeat (all LLM traffic on the z.ai lane, glm-5.3-flash — relay down per HU-2701)

Harness: `experiments/pageindex-eval/` (isolated venv, pageindex SDK
0.2.10 from PyPI, local mode only, NO cloud API; upstream clone at
`/root/repos/pageindex` for reference). Relay rerun on Gemini flash is an
env switch, not a code change.

### 1. Flash smoke — text PDF (q1-fy25-earnings.pdf, 22p, 45k text chars)

- Tree generated end-to-end on glm-5.3-flash: sensible section/subsection
  hierarchy with per-node summaries (Disney earnings structure).
- Structure extraction itself is LLM-FREE (layout/bookmark based); the
  LLM toll is summaries + expand only.
- Wall: ~17 min with concurrency serialized to 1 (z.ai coding lane rate
  limit; SDK default is 64 concurrent calls — harness throttles to 1
  with 429 backoff).
- Usage capture: litellm callbacks proved unreliable under the SDK's
  asyncio path (1 of ~20 calls recorded); replaced with deterministic
  wrapper-side token counting (same methodology for every arm).

### 2. Scan caveat — EMPIRICALLY ANSWERED (reframe item 5)

`outputs/flash-scan-caveat.json`:

| PDF | text chars | outcome |
|---|---|---|
| scanned_formula.pdf | 0 | no text layer — Flash has nothing to index |
| scanned_mixed.pdf | 0 | no text layer — Flash has nothing to index |
| chart_table.pdf | 252 | 0-node structure (degenerate on chart/table page) |
| real_mixed.pdf | 40,407 | 14 nodes in 2.8s LLM-free (bookmarks) |

**The comparison takes shape B for scans**: PageIndex cannot ingest
image-only pages, so it is a candidate COMPONENT behind our extraction
front end — `[Docling -> PageIndex tree]` vs `[Docling -> our
retrieval]` — not a competing pipeline.

### 3. Shape-B composite smoke (Docling -> PageIndex md_to_tree)

All four Docling torture-page extractions build PageIndex trees LLM-free
in <0.1s (`outputs/docling-composite-smoke.json`): real_mixed 28 nodes,
scanned_formula 3, scanned_mixed 2, chart_table 0. Mechanically sound.

## Retrieval head-to-head (run 2, same heartbeat block) — `outputs/h2h-r20260905/`

Harness: `scripts/run_retrieval_h2h.py` (+ `repair_h2h.py`). Gold set of 10
questions hand-authored from the PDF text (5 single-section, 5
multi-page-span — the attack-the-weakness slice), each with page citations.
Arms share answer extraction + judge; only the retrieval leg differs:

- **pageindex_flash**: SDK `client.chat()` — agent navigates the tree
  (browse/structure/page-content tools), glm-5.3-flash, z.ai lane, max 8 turns.
- **flat_bm25** (our-leg proxy): page-level chunks (no cross-page merging) →
  BM25 top-5 → identical FRAMES answer prompt. Retrieval is LLM-free.

Judge: glm-5.3, temp 0, exact FRAMES prompt (BEAM discipline). All lanes
serialized through one `litellm.acompletion` wrapper with deterministic
token counting + 429 backoff.

### Scores

| arm | overall | single | multi-span | tok/query | calls/query | s/query |
|---|---|---|---|---|---|---|
| pageindex_flash | **9/10 = 0.90** | 5/5 = 1.0 | 4/5 = 0.8 | ~20.4k | 3.0 | ~21.5 |
| flat_bm25 | **9/10 = 0.90** | 4/5 = 0.8 | 5/5 = 1.0 | ~5.7k | 1.0 | ~7.2 |

The slices split in opposite directions: PageIndex is perfect on targeted
single-section lookups; the flat leg is perfect on multi-page-span — the
exact weakness Pat called. PageIndex's one span miss (q007) is a synthesis
nuance (answered 124.6M table-sum where the headline figure is 125M; judge
took the headline). Flat's one miss (q004) is answer-stage: its retrieval
hit rate is unchanged but the answerer burned the token cap on reasoning
without emitting the final line.

### Cost

- Per query: PageIndex ~20.4k tokens (3.0 calls) vs flat ~5.7k tokens
  (1 call) → **3.6× token cost, 3.0× latency for zero net accuracy gain**.
- Per document indexed: Flash tree-gen 329 tok/page (1 LLM call for 22p;
  structure extraction is LLM-free) vs flat page-chunk+BM25 = **0 tokens**.
- $ rates pending a confirmed z.ai rate card; token counts are the
  lane-independent unit (same methodology both arms, gpt-4o tokenizer).
- PageIndex retrieval cost is fused with answering (SDK chat design);
  per-leg separation would need an SDK fork — recorded, not worked around.

### Harness integrity note

First scoring pass had 5 artifacts with empty `message.content` (glm-5.3
reasoning tokens consumed the cap before content; payload sat in
`reasoning_content`). `chat_once` now falls back to reasoning_content with
a 4x-cap retry; the 5 affected items were re-run and the audit trail is in
`scores.json.repair`. Post-repair, no empty verdicts remain.

### Verdict (one page, per deliverable)

On this 10-question gold set over one 22-page text PDF, the flat leg
**matches PageIndex Flash retrieval quality (0.90 vs 0.90) at ~28% of the
token cost** and wins the multi-page-span slice. Per the reframe's success
criterion (match-or-beat at a fraction of the cost), PageIndex Flash is
**evaluated-and-not-adopted as the retrieval leg on this evidence**. The
tree-index remains interesting as a structure layer for PDF-native
ingestion (shape B composite), and as an organization/traceability layer —
not as the retrieval mechanism we route queries through.

Scope caveats (recorded, not hidden): n=10, single document, hand-authored
gold, judge=glm-5.3 on the z.ai lane, one model lane (Gemini rerun pending
HU-2701 relay). The FRAMES wiki-corpus comparison (via `md_to_tree`) is the
follow-up that would test corpus-scale generality before this verdict is
treated as categorical.

## FRAMES corpus pass (run 3) — `outputs/frames-md-r20260905/`

The "measured FRAMES numbers vs baseline" deliverable: PageIndex Flash
agent retrieval on the exact r20260905b sample (20 questions, 79-article
wiki corpus, same glm-5.3 judge).

Harness: `scripts/run_frames_md_h2h.py`. Wiki plaintext has no native
structure, so each article becomes a markdown doc; `md_to_tree` builds the
tree with node summaries ON (glm-5.3-flash, Flash product behavior), and
markdown line ranges map to 60-line pseudo-pages so the SDK's page-based
agent tools work unmodified. Docs inserted via the public `DocStore`
API — no fork. Store persists; reruns resume.

| arm | score | tok/query (chat) | calls/query | s/query |
|---|---|---|---|---|
| PageIndex agent retrieval | **16/20 = 0.80** | ~40.3k | 5.1 | ~100 |
| flat pipeline (r20260905b) | **12/20 = 0.60** | ~1.7k context | — | ~4 |

Overlap: both correct 11, **PI-only 5**, baseline-only 1, neither 3.
Index cost: 5.8k tok/article (73 calls for 79 docs, summaries batched).

Per-question deltas: PI flips q129, q624, q663, q672, q729 to correct
(multi-hop questions the flat stack missed); it loses q749 (baseline got
it). q330/q444 stay open for both. q716's first pass hit `max_turns=8`
(harness param); re-run at 16 turns answered and was judged INCORRECT —
final 20/20 scored.

### Verdict (revised — supersedes the single-doc verdict above)

The two measurements compose into one answer:

- **Single-document lookups** (earnings PDF, 10q): flat matches PageIndex
  (0.90 = 0.90) at ~28% of the token cost. Flat stays the default path.
- **Corpus-wide multi-hop retrieval** (FRAMES, 20q): PageIndex wins
  decisively, 0.80 vs 0.60, flipping five questions the flat stack missed
  — at ~24× the per-query tokens (40.3k vs 1.7k).

Per the original adoption gate ("adopt only if it beats or meaningfully
complements the measured baseline"), this is a **beat** on the hard
retrieval slice. Recommendation: PageIndex-style tree+reasoning retrieval
is the **escalation lane for hard multi-hop queries** (router pattern:
flat first, tree-reasoning on low-confidence), not the default retrieval
path — the cost asymmetry is 24×, and 3 of 4 flat misses stayed missed for
PI too (neither=3). No vector store enters the architecture either way.

Scope caveats: FRAMES arm indexes plaintext wiki (no native structure —
weaker than Flash on structured PDFs); pseudo-page mapping is
harness-side; judge branch tokens for run 1 not persisted (excluded from
metered figures); baseline answerer was glm-5.3 vs PI chat glm-5.3-flash
(lane-consistent, model-different — recorded). Gemini-flash rerun still
blocked on the HU-2701 relay.

## Next (retrieval leg — the actual head-to-head)

~~1. PageIndex reasoning retrieval vs our flat baseline on the same sample~~
**done this block** (`outputs/h2h-r20260905/`) — see scores above.
~~3. $/page indexed + $/query at retrieval~~ **done this block** (token
units; $ pending rate card).

2. FRAMES wiki-corpus pass via `md_to_tree` (corpus-scale generality).
   **done this block** — see FRAMES section above.
4. Gemini-flash tree-gen rerun once the HU-2701 relay is back; cost/quality
   comparison across lanes. **done 2026-09-12** — see the lane-rerun section below.
5. Vault record after measurement (measure-first doctrine) — this doc is
   the measurement; Librarian record follows founder/CEO review.

## Founder paths-not-taken (for the eventual vault record)

Original PageIndex: rejected by Pat in production for resource weight —
stores each page as an image, multi-page content left to interpretation,
constant LLM interpretation toll. Flash (text-based tree-gen, just
released) is the unproven successor being benchmarked here.

## Lane rerun — Gemini flash tree-gen (2026-09-12, HU-2726)

The founder-directive rerun: same 22-page q1-fy25-earnings.pdf, same
UsageLedger methodology, every LLM call on the flash ladder
(`gemini/gemini-3.8-flash`) via litellm's native gemini provider.
Harness: `scripts/run_flash_smoke_gemini.py` (prep'd in 8f18691; this run
also fixed a latent `new_client()` signature bug that would have
TypeErrored the Gemini arm — it had never executed, committed blocked).

### Preconditions, verified empirically (not assumed)

- `GEMINI_API_KEY` provisioned in the repo `.env` on 2026-09-12 (blocker
  resolved out-of-thread; `.env` comment references this issue).
- Direct egress to `generativelanguage.googleapis.com` is **geo-blocked**
  from this box (HTTP 400 `User location is not supported`) — the
  pat-w11pc SOCKS5 relay is **required**, not a fallback.
- Relay + key end-to-end: keyed `models.list` through
  `socks5://100.83.231.16:1080` → HTTP 200, `gemini-3.8-flash` present;
  ~30 KB POST (summary-prompt scale) → 200 with generation.
- Transport note for any rerun: litellm 1.100.x defaults to its aiohttp
  transport, which cannot do SOCKS proxies (connection resets). Env
  `DISABLE_AIOHTTP_TRANSPORT=True` + `ALL_PROXY=socks5://…` switches it
  to httpx, which tunnels cleanly. Isolated venv rebuilt this run
  (pageindex 0.2.10 from PyPI — the Sep-5 venv did not survive the box).

### Results — index-only, 22p, serialized concurrency, gpt-4o tokenizer

| metric | z.ai glm-5.3-flash (HU-2723, as recorded) | Gemini gemini-3.8-flash (this run) | FRAMES-md index (79 wiki docs) |
|---|---|---|---|
| LLM calls (index) | 1 recorded | 18 | 73 (~0.9/doc, summaries batched) |
| total tokens (index) | 7,239 recorded | **20,102** (14,897 in / 5,205 out) | 5.8k/article |
| tokens/page | 329 recorded | **913.7** | — |
| s/page | 47.0 (wall 1034 s, 429-backoff-laden) | **4.03** (wall 88.6 s) | — |
| tree | "sensible section/subsection hierarchy" (raw storage never committed) | 18 nodes, depth 2, spans p1–22, summaries on all 18 (avg 506 chars) | wiki md_to_tree |

### Measurement-integrity caveat on the z.ai figures

The z.ai arm's `llm_calls: 1` is inconsistent with the same SDK 0.2.10
code path measured here: `summarize_tree` issues one call per summary-
eligible node (18 for this corpus — the structure pass is deterministic
and LLM-free on both lanes), and no batching exists in the flash PDF
path. One recorded call carrying 6,713 prompt tokens matches the
callback-era under-capture this same document warns about above ("1 of
~20 calls recorded"). The recorded 7,239 tokens (329 tok/page) is
therefore very likely an **under-capture**, and the honest cross-lane
token comparison is: prompts are byte-identical per node across lanes,
so Gemini's 20.1k total is the expected order of the true z.ai toll;
the z.ai per-call completion delta is unrecoverable from the stored
record. Recorded as-is; not worked around.

### Tree quality (Gemini arm)

Top level mirrors the Disney earnings document: title/summary page →
guidance+CEO commentary → summarized results → segment discussion (Star
India / Entertainment / Sports / Experiences children) → other financial
information (4 children) → income statement → balance sheets → DTC
definitions → non-GAAP → forward-looking statements. All 18 nodes carry
page spans covering p1–22 and a summary. This matches the recorded
z.ai-run description qualitatively; a node-level lane diff is not
possible because the z.ai raw tree was never committed (`.pageindex/`
is gitignored) and the box was since pruned. The Gemini tree snapshot
is committed at
`outputs/smoke-gemini-q1fy25/tree_snapshot.json` so the claim is
checkable this time.

### Verdict

The flash tree-gen lane is portable across the flash ladder: identical
harness, identical deterministic structure, coherent tree on
gemini-3.8-flash through the relay at ~20.1k tokens (≈914 tok/page,
289 tok/summary) and 4.03 s/page — 11.7× faster wall than the z.ai
arm's recorded run, though most of that gap is the z.ai lane's 429
backoff ladder, not model speed. The z.ai token figures recorded in the
HU-2723 section should be read as lower bounds (capture bug), not as a
Gemini cost premium. Gemini lane egress requires the pat-w11pc relay
(direct is geo-blocked); transport recipe recorded above.
