---
tags: [datasets, research, huggingface, kaggle, github, catalog, emotion, persona]
updated: 2026-08-26
---

# HF / Kaggle / GitHub — Emotional Voice & Expression Dataset Sweep

Companion to [[hume-ai-datasets-catalog]] (owner order 2026-08-26). Discovery
sweep across **HuggingFace, Kaggle, and GitHub** for datasets relevant to
HUible's persona/memory use case: emotional & conversational speech, vocal
bursts (laughter/sighs), prosody, dyadic conversations, facial expression
(FACS), and multimodal emotion recognition.

## Method

- Date: 2026-08-26 (R&D Lead, HU-2114).
- Live API sweeps: HuggingFace datasets API (keyword + canonical-name
  queries), Kaggle public dataset-list API, GitHub search API. License /
  size fields read from live metadata where possible; canonical upstream
  terms take precedence over mirror tags.
- Index cross-check: SuperKogito/SER-datasets (77 curated SER datasets
  w/ licenses), EvelynFan/AWESOME-FER.
- ⚠ = **licensing/consent flag** (see "Licensing posture" below).

## A. Emotional / expressive speech (audio, acted + natural)

| Dataset | Platform / link | Modality | Size | License | Notes |
|---|---|---|---|---|---|
| CREMA-D | [GitHub](https://github.com/CheyneyComputerScience/CREMA-D) (548★) · [HF](https://huggingface.co/datasets/MahiA/CREMA-D) · [Kaggle](https://www.kaggle.com/datasets/ejlok1/cremad) | audio+video | 7,442 clips, 91 actors, 6 emotions | **ODbL / ODC-By** | Best-known open English acted-emotion set; HF/Kaggle mirrors carry permissive tags consistent with upstream |
| RAVDESS | [Zenodo](https://zenodo.org/record/1188976) · [Kaggle](https://www.kaggle.com/datasets/uwrfkaggler/ravdess-emotional-speech-audio) · HF mirrors | audio+video | 7,356 files, 24 actors, speech+song | ⚠ CC BY-NC-SA 4.0 | Multimodal (facial+vocal); NC blocks commercial use |
| TESS | [U Toronto Dataverse](https://tspace.library.utoronto.ca/handle/1807/24487) · Kaggle (ejlok1) | audio | 2,800 clips, 2 actresses, 7 emotions | ⚠ CC BY-NC-ND 4.0 | NoDerivs also blocks derivative training redistribution debates; research |
| EMO-DB (Berlin) | [emoDB](http://emodb.bilderbar.info/index-1280.html) · Kaggle (piyushagni5) | audio | 800 utterances, 10 actors, German | research, no formal license | Classic benchmark; small |
| SAVEE | [Surrey](http://kahlan.eps.surrey.ac.uk/savee/Database.html) · Kaggle (ejlok1) | audio+video | 480 utterances, 4 male actors | ⚠ research-only (free) | |
| ESD | [HLT Singapore](https://hltsingapore.github.io/ESD/) | audio+text | 29 h, 20 speakers (10 EN + 10 ZH), 5 emotions | ⚠ academic license | Large bilingual emotional set |
| MSP-Podcast | [UT Dallas](https://ecs.utdallas.edu/research/researchlabs/msp-lab/MSP-Podcast.html) · HF mirrors exist (CLAPv2) | audio | 100 h+ (v1.x), natural podcasts, V/A/D + categorical | ⚠ academic EULA; commercial license sold | Largest naturalistic English emotion corpus; HF mirrors are unofficial re-uploads |
| MSP-IMPROV | [UT Dallas](https://ecs.utdallas.edu/research/researchlabs/msp-lab/MSP-Improv.html) | audio+video | 20 sentences × 12 actors, **dyadic improv** | ⚠ academic + commercial license | |
| Dusha | [GitHub (salute-developers)](https://github.com/salute-developers/golos/tree/master/dusha) · HF (skb50/… no, branded Dusha) | audio | ~350 h / 300k recordings, Russian, acted + real | open w/ attribution (custom) | Largest permissive-ish emotional corpus found; check their license PDF for commercial |
| THAI SER | [GitHub (vistec-AI)](https://github.com/vistec-AI/dataset-releases/releases/tag/v1) | audio | 41 h, 27,854 utts, 200 actors | CC BY-SA 4.0 | |
| EmoV-DB | [GitHub (numediart)](https://github.com/numediart/EmoV-DB) (284★) · HF mirrors | audio | 4 speakers, amused/angry/disgusted/neutral/sleepy | ⚠ no explicit license | "Amused" + "sleepy" styles are rare labels; prosody-valuable |
| JL corpus | [Kaggle](https://www.kaggle.com/datasets/tli725/jl-corpus) | audio | 2,400 clips, 4 actors, 5+5 emotion labels | **CC0 1.0** | Most permissive acted-emotion set found |
| Thorsten-Voice (emotional) | [Zenodo](https://zenodo.org/records/5525023) | audio | 2,400 clips, 1 speaker (German), amusement/disgust/anger/surprise/neutral + drunk/whisper/sleepy | **CC0** | Single-speaker but truly public-domain emotional states |
| EMNS | [OpenSLR 136](http://www.openslr.org/136/) | audio | 1,206 utts, 1 British female speaker, 8 states + sarcasm | **Apache-2.0** | Narrative-storytelling emotional style |
| nEMO | [GitHub (amu-cai)](https://github.com/amu-cai/nEMO) | audio | 3 h, 9 actors, Polish, 6 emotions | CC BY 4.0 | |
| ASVP-ESD | [Kaggle](https://www.kaggle.com/datasets/dejolilandry/asvpesdspeech-nonspeech-emotional-utterances) | audio | ~13,285 clips incl. non-speech | ⚠ Unknown; scraped from movies/TV/YouTube | Includes pain/pleasure/boredom labels; provenance risk |
| EMOVIE | [release page](https://viem-ccy.github.io/EMOVIE/dataset_release.html) | audio | 9,724 clips, Mandarin | ⚠ CC BY-NC-SA 2.0 | |
| MDER / EMOVOME / MESD etc. | Zenodo/Mendeley (see SER-datasets index) | audio | various (Arabic/Spanish/Mexican) | mostly CC BY 4.0 | Long tail of CC-BY national corpora — fine for multilingual |

## B. Vocal bursts, laughter & non-speech vocalizations

| Dataset | Platform / link | Modality | Size | License | Notes |
|---|---|---|---|---|---|
| VIVAE | [Zenodo](https://zenodo.org/record/4066235) | audio | 1,085 non-speech clips, 11 speakers, 6 affects × 4 intensities | ⚠ CC BY-NC-SA 4.0 | The canonical non-speech vocal-burst research set (pain/pleasure/surprise…) |
| Keio-ESD | [NII-SRC](http://research.nii.ac.jp/src/en/Keio-ESD.html) | audio | 1 male speaker, **47 emotion categories** | ⚠ research-only | Richest single-taxonomy emotional vocal set; tiny |
| Hume/Cowen vocal-burst corpora | via [Hume publications](https://www.hume.ai/publications) (Vaccaro, Cowen et al.) — OSF supplements | audio | ~2.5k+ bursts, 20+ emotion dims | varies (per-paper OSF terms) | Hume's own research lineage for bursts; not a bulk catalog, per-paper requests |
| LAION vocal bursts | [HF: laion/vocal-bursts-clean](https://huggingface.co/datasets/laion/vocal-bursts-clean) + more + [GitHub pipeline](https://github.com/LAION-AI/vocal-burst-annotation-pipeline) | audio | 1M–10M clips (HF size class) | **Apache-2.0** | **Synthetic** (TTS-generated) bursts — consent-safe by construction; used for TTS expressiveness |
| AMI laughter annotations | [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/download/) | audio+video | ~100 h meetings, laughter word-level annotations | CC BY 4.0 (annotations) | Natural multiparty laughter in context |
| Switchboard laughter subsets | LDC (paid) — laughter-annotated subsets circulate in papers | audio | ~2.5k laughs | ⚠ LDC license | Classic laughter-detection source; not openly downloadable |

## C. Prosody & expressive delivery

| Dataset | Platform / link | Modality | Size | License | Notes |
|---|---|---|---|---|---|
| Att-HACK | [OpenSLR 88](http://www.openslr.org/88/) | audio | ~30 h, 25 speakers, 4 social attitudes (friendly/distant/dominant/seductive), French | ⚠ CC BY-NC-ND 4.0 | Social-attitude prosody — directly persona-relevant, but NC |
| ProsAudit | [HF: DynamicSuperb/ProsodyNaturalness_ProsAudit](https://huggingface.co/datasets/DynamicSuperb/ProsodyNaturalness_ProsAudit-Protosyntax) | audio | benchmark pairs (prosody naturalness/syntax) | research (challenge data) | Prosody evaluation set for model benchmarking |
| EmoV-DB (also §A) | see above | audio | see above | ⚠ | Amused/sleepy speaking styles = prosody variety |
| LibriTTS / LibriTTS-R | [OpenSLR 60/141](https://www.openslr.org/60) | audio | 585 h expressive audiobook read speech, 245 h aligned | **CC BY 4.0** | Clean, commercial-ok prosody-rich read speech at scale |
| DailyTalk | [GitHub (keonlee9420/DailyTalk)](https://github.com/keonlee9420/DailyTalk) (260★) · [HF](https://huggingface.co/datasets/eustlb/dailytalk) | audio+text | 6.3k dialogues from public-domain audiobooks | MIT (repo); content public domain | Read-style *dialogue* with turn structure — prosody-in-conversation |
| Thorsten emotional (also §A) | Zenodo | audio | see above | **CC0** | Emotional delivery single voice |

## D. Dyadic & multiparty conversation

| Dataset | Platform / link | Modality | Size | License | Notes |
|---|---|---|---|---|---|
| IEMOCAP | [USC SAIL](https://sail.usc.edu/iemocap/iemocap_release.htm) · HF/Kaggle mirrors exist | audio+video+mocap+text | 12 h, 10 actors in **5 dyadic sessions**, V/A/D + categorical | ⚠ EULA (research; commercial negotiation) | THE dyadic emotional benchmark. ⚠ Kaggle/HF full mirrors (e.g. dejolilandry/iemocapfullrelease, "Unknown" license) are unofficial re-uploads — not a compliant acquisition path |
| SEMAINE | [semaine-db.eu](https://semaine-db.eu/) | audio+video+text | 95 dyadic conversations, 21 subjects, 5-dim FeelTrace | ⚠ academic EULA | Sensitive-artificial-agent dyads; valence/activation traces |
| MSP-IMPROV (also §A) | UT Dallas | audio+video | dyadic improvised interactions | ⚠ academic + commercial | |
| CANDOR | [candorcorpus.org](https://candorcorpus.org) · [HF annotations](https://huggingface.co/datasets/hiraki/candor-turntaking-annotations) | audio+video+text | **~1,650 h** natural conversation, turn-taking annotated | free for research w/ registration | Largest open-ish natural conversation corpus; registration gate |
| AMI Meeting Corpus | [AMI](https://groups.inf.ed.ac.uk/ami/download/) | audio+video+text | ~100 h multiparty meetings | open for research; annotations CC BY 4.0 | Overlap/interruption dynamics |
| HCRC Map Task | [HCRC](https://groups.inf.ed.ac.uk/maptask/) | audio+text | 128 task dialogues | free for research | Classic grounding/repair dyads |
| Switchboard / Fisher | LDC | audio+text | 2.4k / 20k+ telephone conversations | ⚠ LDC paid license | Gold-standard telephone dyads; cost per seat |
| AnnoMI | [GitHub (uccollab)](https://github.com/uccollab/AnnoMI) · [HF](https://huggingface.co/datasets/to-be/annomi-motivational-interviewing-therapy-conversations) | audio+video+text | 133 therapy conversations, ~9k annotated utterances, **dyadic (client/therapist)** | open (openrail on HF mirror) | High-empathy relational talk — most persona-relevant open dyadic set found |
| MELD (also §F) | [GitHub (declare-lab)](https://github.com/declare-lab/MELD) (1,079★) | audio+video+text | 1,400 dialogues / 14k utterances, *Friends* clips, multiparty | ⚠ GPL-3.0 repo; content derived from copyrighted TV → research | Conversational emotion benchmark; ⚠ Kaggle mirror mislabeled "CC0" |
| M3ED | paper: arXiv:2202.13059 (data via application form) | audio+video+text | 990 dyadic dialogues, 24 emotions, Mandarin | ⚠ request access | Multimodal dyadic Chinese |
| DaiKon (Hume) | [ACII 2026 workshop](https://www.hume.ai/research) — challenge registration | audio+video | tbd | challenge terms | Hume's own dyadic-conversations data lane; see hume-ai-datasets-catalog |

## E. Facial expression & FACS

| Dataset | Platform / link | Modality | Size | License | Notes |
|---|---|---|---|---|---|
| FER2013 | [Kaggle](https://www.kaggle.com/datasets/deadskull7/fer2013) (+ many mirrors) | images | ~35.9k 48×48 faces, 7 classes | open (competition data) | The default FER starter set |
| CK+ | mirrors on Kaggle (⚠ davilsena/ckdataset etc.) | images+video | 593 sequences, 123 subjects, **AU-annotated** | ⚠ upstream license-required; Kaggle copies unofficial | Posed AU benchmark |
| AffectNet | [official](http://mohammadmahoor.com/affectnet/) · Kaggle mirrors ⚠ | images | ~1M images (450k annotated), 11 categories + valence/arousal | ⚠ academic request; commercial license sold | Largest static V/A face set |
| RAF-DB | [official](http://www.whdeng.cn/raf/model1.html) | images | ~30k real-world faces, basic+compound | ⚠ research request | In-the-wild FER baseline |
| SAMM / DISFA / BP4D(+) | university request forms | video | spontaneous **FACS AU** coded | ⚠ academic EULA each | The AU ground truths; all gated |
| FERA 2015/2017 | challenge registration | video | BP4D-derived AU | ⚠ challenge terms | |
| EmotioNet | [OSU](http://cbcsl.ece.ohio-state.edu/emotionnet.html) | images | ~1M w/ AU annotations | ⚠ database request | Largest AU-annotated image set |
| MAFW | [mafw-database.github.io](https://mafw-database.github.io/MAFW/) | video+audio | 10,045 in-the-wild clips, 11 single + 32 compound emotions | ⚠ non-commercial research | Compound emotions + audio |
| OpenFace (toolkit) | [GitHub](https://github.com/TadasBaltrusaitis/OpenFace) (7.7k★) | tool | AU recognition, gaze, landmarks | code free for research; **commercial via CMU flintbox** | Not a dataset — the standard tool to *generate* AU labels on our own video |

## F. Multimodal emotion recognition (tri-modal benchmarks)

| Dataset | Platform / link | Modality | Size | License | Notes |
|---|---|---|---|---|---|
| CMU-MOSI | [site](https://github.com/CMU-MultiComp-Lab/CMU-MultimodalSDK) (360★, MIT code) · HF mirrors | A+V+T | 2,199 utterances, 93 monologue videos | ⚠ SDK MIT; **data research-only** (YouTube-derived) | Sentiment 7-pt scale |
| CMU-MOSEI | same SDK · [HF](https://huggingface.co/datasets/tamb2203579/CMU-MOSEI) | A+V+T | 65 h, 23k utterances, 1k+ speakers | ⚠ same | The standard multimodal ER benchmark |
| MELD | see §D | A+V+T | 14k utterances | ⚠ research | Conversational, multiparty |
| IEMOCAP | see §D | A+V+T+mocap | 12 h dyadic | ⚠ EULA | |
| CH-SIMS / v2 | [GitHub (thuiar)](https://github.com/thuiar/ch-sims-v2) (102★) | A+V+T | 2,281 segmented videos (v2), Mandarin | research (cite paper) | Chinese multimodal sentiment w/ unimodal labels |
| RECOLA | [official](https://diuf.unifr.ch/main/diva/recola/download.html) | A+V+physio | 3.8 h, 46 participants, dyadic remote collaboration | ⚠ academic + commercial | Continuous V/A annotations |
| SEWA | [db.sewaproject.eu](https://db.sewaproject.eu/) | A+V+physio | 2k+ min, 398 subjects, 6 cultures | ⚠ EULA | In-the-wild multimodal affect |
| MuSe-CAR | [Zenodo](https://zenodo.org/record/4134758) | A+V+T | 40 h car-review vlogs | ⚠ academic + commercial | MuSe challenge line |

## G. Indexes & tooling (maintenance shortcuts)

| Resource | Link | Use |
|---|---|---|
| SER-datasets | [GitHub](https://github.com/SuperKogito/SER-datasets) (420★) | 77-dataset SER index w/ licenses, sizes, links — keep as canon |
| AWESOME-FER | [GitHub](https://github.com/EvelynFan/AWESOME-FER) (961★) | FER paper/benchmark index |
| EmoBox | arXiv:2406.07162 (+ HF `EmoBox`) | Multilingual SER benchmark unifying many of the above |
| EMO-SUPERB | [GitHub](https://github.com/ag027592/EMO-SUPERB) | Leakage-free SER benchmark |
| nkululeko / ERTK | GitHub | Experiment tooling w/ loaders for most corpora above |

## Reference points (per owner order)

- Hume publications: **RW-Voice-EQ Bench** (arXiv 2026-07), **DaiKon** dyadic
  workshop/challenge (ACII 2026-05), **TADA** text-acoustic dual alignment
  (arXiv 2026-02) — see [[hume-ai-datasets-catalog]].
- github.com/humeai org (verified 2026-08-26): no public dataset repos.
  Top: `tada` (1,009★), EVI SDKs/starters (MIT), `hume-research-publications`
  (PDFs), `competitions` (challenge material), `expressive-tts-arena` (MIT),
  `wsds` (MIT). Dataset acquisition for Hume corpora stays license-gated.

## Licensing posture (routing guide for Tech Lead)

**Red — research-only / EULA (no commercial use, no voice cloning):**
IEMOCAP, MSP-Podcast, MSP-IMPROV, SEMAINE, RECOLA, SEWA, GEMEP, DEMoS,
EmoFilm, VESUS, LSSED, SAVEE, Keio-ESD, OGVC, VERBO, CK+, AffectNet,
SAMM, DISFA, BP4D, EmotioNet, FERA, M3ED, MuSe-CAR, MAFW, Switchboard/Fisher
(LDC paid). Also CMU-MOSI/MOSEI **data** despite MIT SDK.

**Orange — CC NC/ND (viral non-commercial):** RAVDESS, TESS, VIVAE, EMOVIE,
CaFE, Att-HACK, OMG-Emotion, ESD (academic), ShEMO (unspecified).

**Orange — derivative-content risk (underlying media copyrighted):**
MELD (*Friends*), ASVP-ESD / EmoFilm (movies/TV), CMU-MOSI/MOSEI (YouTube).
Fine for internal research baselines; not for redistribution or trained-model
commercial claims without review.

**Mirror hazard:** HF/Kaggle re-uploads routinely mislabel — e.g. Kaggle
IEMOCAP full release ("Unknown"), MELD mirror tagged "CC0", CK+ mirrors,
HF `MahiA/CREMA-D` tagged "MIT" while upstream is ODbL. **Acquisition must
follow the upstream license source**, never the mirror tag.

**Green — commercially usable with attribution (recognition/prosody
training):** CREMA-D (ODbL), JL corpus (CC0), Thorsten emotional (CC0),
EMNS (Apache-2.0), nEmo (CC BY 4.0), LibriTTS(-R) (CC BY 4.0), FER2013
(open), AISHELL-3 (Apache-2.0), Dusha (custom attribution — read PDF),
THAI SER (CC BY-SA 4.0), AMI annotations (CC BY 4.0), AnnoMI (open),
LAION vocal bursts (Apache-2.0, synthetic).

**Voice-cloning consent (hard rule):** a dataset license ≠ per-speaker
cloning consent. Even CC0 corpora (JL, Thorsten) contain real human voices
whose actors never agreed to be *imitated*. Route:
1. **Expression recognition / prosody modeling** → any Green (or Red with
   license) corpus is fine as training signal.
2. **Persona voice generation** → synthetic-only corpora (LAION vocal
   bursts) or licensed/commissioned recordings of our own talent. Never
   fine-tune a TTS voice on a specific upstream speaker (RAVDESS/IEMOCAP/
   EmoV-DB actors) without separate consent.

## Relevance to HUible persona/memory work

- **Empathy layer (near-term):** AnnoMI + AMI/CANDOR give open dyadic,
  high-empathy conversational dynamics for the memory/dialogue models.
- **Vocal expressiveness (voice roadmap):** LAION synthetic bursts are the
  consent-safe path; VIVAE/Keio-ESD/Hume OSF corpora are the taxonomy
  references for *what* to express.
- **Prosody persona:** LibriTTS-R + DailyTalk (CC BY/public-domain) anchor
  commercial-safe prosody; Att-HACK is the attitude taxonomy reference
  (research-only).
- **FACS/multimodal:** everything gold-standard is gated; realistic path is
  FER2013/RAF-DB-class open data + OpenFace-generated AU labels on
  consented video (our own recordings).

## Change log

- 2026-08-26 — initial sweep: HF/Kaggle/GitHub live API queries + index
  cross-check; catalog + licensing/consent routing (R&D Lead, HU-2114).
