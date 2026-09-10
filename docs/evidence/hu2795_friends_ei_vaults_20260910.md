# HU-2795 — Friends EI Vaults: Chandler + Monica (evidence)

**Date:** 2026-09-10 · **Agent:** R&D Lead (1a0bd97a) · **Repo:** /root/repos/eq-vaults
**Deliverable question:** do the Chandler + Monica EI vault subsets, built to
spec v0.2, pass founder review as usable, realistic reference vaults?

## Shipped

| Item | Where |
|---|---|
| Chandler-EI vault | `data/chandler-ei/` — 230 notes + index + contrast-pair |
| Monica-EI vault | `data/monica-ei/` — 235 notes + index + contrast-pair |
| Builder (deterministic) | `pipeline/friends_ei_build.py` |
| Measurement runner | `pipeline/friends_ei_measure.py` |
| Validator | `pipeline/friends_ei_validate.py` |
| Provenance ledger (outside vaults) | `provenance/friends-ei/ledger.json` + `media-join.json` + `measurements-{audio,text}.jsonl` |
| Kestra flow | `pipeline/flows/friends_ei_vaults.yaml` → deployed `pat.ingest/friends_ei_vaults` (kestra2 :8082, rev 2) |
| Publish script | `pipeline/publish_friends_ei.sh` |
| GitHub (private) | `patbhakta/vault-ei-chandler-ei`, `patbhakta/vault-ei-monica-ei` |
| FNS (review surface) | vaults `Chandler-EI` (id 11), `Monica-EI` (id 12), seeded + verified 3/3 round-trip, git-sync configured |

## Founder-realism shape (per wake payload)

- Mostly TEXT: 146/230 (63%) and 150/235 (64%) beats are text-only.
- SOME audio: 85 clip-linked beats per vault — LINKED by md5 + path + URL,
  never embedded (validator enforces zero `![` embeds). 5,279 MELD-format
  clips held; only duration-verified unambiguous joins attach (4,842 usable).
- Video: none held — stated honestly in ledger/index, nothing fabricated.
- Photos: flagged in index for the later phase.
- Premium only: `what` (plain-human), verbatim beat, `why` (trigger →
  disposition → context, disposition table attributed), human time
  (age-at-moment computed from canon birthdates + estimated air date,
  ±3wk rule documented), before/after traversal, relationship-state +
  register, golden-memory flag (milestone episodes + season boundaries).
- Sparse > complete: 230/235 of ~1,600 extractable beats per character.

## Gate inputs honored (v0.2 decision 4)

HU-2790 gate PASSED 2026-09-10 (friends-gold-v2, honest bars: audio
0.300/0.275, text 0.257/0.213). **Update (HU-2798):** gold re-issued as
**v2.1** with the split-safe duration-unique join; gate re-scored, canonical
bars now **audio 0.357/0.314, text 0.175/0.207** (`gate-v2.1-results.json`).
Conclusions unchanged — emotion2vec incumbent strengthened. Vault artifacts
verified unaffected: this builder already used the safe `(split,dia,utt)` +
duration-unique join, and v2.1's join owners are byte-identical to
`provenance/friends-ei/media-join.json` (4,842).
`readings.huible-local-v1` filled with
- audio lane: emotion2vec_plus_large (per-clip, beat = mean of clip dists)
- text lane: roberta-base-go_emotions (top-5 raw + basic-7 pred)
- fused with `meld-human`; `agreement` recorded per note; conflicts kept
  side by side (e.g. mo-s07e15-d833-u2: human anger + audio anger 0.701,
  text neutral — all three visible).

## Contrast pair (founder judgment piece)

Same trigger class (romance/commitment confronted directly):
- **Early** `ch-s01e04-d466-u1` — s01e04, age 27.5, single: sincerity only
  leaks sideways through a bit.
- **Late** `ch-s07e19-d99-u2` — s07e19, age 33.8, engaged: intimacy stated
  plainly, deflection reduced to a stutter.
- **Monica arc moment** `mo-s07e15-d833-u2` — s07e15, age 31.8: 4-turn
  sustained anger; wedding control-panic (mirror disposition).
Both Chandler beats carry verified clips; docs at each vault's
`contrast-pair.md`.

## Validation evidence

- `friends_ei_validate.py`: FAIL 0 — schema fields, dangling links, md5 vs
  disk, no embeds, age range 24–40, era tags, season spread s1–s9 both
  vaults, ledger rows == notes (465).
- Determinism: full rebuild `--skip-measure` → byte-identical tree (md5
  manifest diff empty).
- Kestra execution `695ZA0VbJT4D9Tt8p9WKJr`: build SUCCESS · validate
  SUCCESS · publish SUCCESS (in-container stdlib python via join cache).
- Measurements: 253 clips (audio) + 465 beats (text), CPU-only, zero spend,
  ~3m runtime on the gate venv.

## Data-hygiene finding (follow-up — HU-2790 owner: R&D Lead)

MELD `(Dialogue_ID, Utterance_ID)` keys collide across train/dev/test
(2,067 keys; 1,000 with an on-disk same-named clip). This builder joins on
`(split, dia, utt)` + duration-unique matching. The HU-2790 gold builder
(`build_gold_v2.py`) joined on bare `(dia,utt)` with last-split-wins, so a
minority of gold samples may pair a clip with the wrong split's row
(text/label vs audio). Gate conclusions are unlikely to flip (bars already
honest-low), but the gold set should be re-issued as v2.1 with the
duration-unique join. Filed as a Paperclip follow-up issue.

## FNS/kv note

`pat.ingest` kv `FNS_JWT` was stale (old FNS session) — first flow run
created no vault rows (seeds silently "ok" on HTTP-200 error bodies).
Fixed: kv refreshed with the current FNS JWT, vaults created, seeded and
round-trip verified; git-sync configured. Future flow runs should seed
cleanly. (Known cosmetic gap: FNS kv GET displays a truncated value; the
stored value is full.)

## Provenance / licenses

- MELD rows: GPL (attribution). Media: © WBD via MELD/Hume S3 — internal
  only, no redistribution. EmotionX: CC-BY-NC-ND internal — registered,
  NOT held, not joined. Measurement models: emotion2vec (research ckpt),
  roberta-base-go_emotions (MIT) — internal outputs.
