# Hume AI Dataset Catalog

- **Collected:** 2026-08-26 (JARVIS, per Pat's order)
- **Primary source:** https://www.hume.ai/datasets (+ /research, /publications)
- **Purpose:** reference catalog for the huible.com Datasets page; S3 media links verified live (HTTP 200, public-read)
- **Provenance:** scraped from live pages; per-file URLs in `s3-index.json`

## Company snapshot

- Hume AI — "Building AI with emotional intelligence"
- Platform: Kairos Simulate + Evaluate, Expression Measurement API, Human Feedback API, Data Solutions
- Datasets marketing page: 50+ languages, 48+ emotion dimensions, 600+ voice descriptors

## Catalog — Voice AI datasets (commercial, license/request access)

| Category | Description |
|---|---|
| Conversational Audio | Turn-taking, interruptions, multi-speaker dynamics. |
| Emotional Reproduction | Fine-grained annotations across a wide range of expressive speech. |
| Multilingual Audio | Native speaker recordings across global languages and dialects. |
| Voice Realism | Prosody, intonation, pacing, and expressive range. |
| Domain-Specific | Industry contexts: healthcare, education, customer service. |
| Task-Specific | Conversations for assistants, support, tutoring, and research. |

## Catalog — Expression & Multimodal (research-page samplers)

| Category | Modality | Description |
|---|---|---|
| Speech Prosody | audio | Annotated speech rhythm, stress, and intonation patterns across diverse speakers and emotional contexts. |
| Vocal Expression | audio | Voice timbre, resonance, and vocal quality samples labeled across 48 emotion dimensions. |
| Vocal Bursts | audio | Laughter, sighs, gasps, and non-verbal vocalizations categorized by type and emotional context. |
| FACS 2.0 | visual | Facial Action Coding System data with action unit annotations and intensity scoring. |
| Dynamic Reaction | visual+audio | Temporal expression sequences capturing how facial and vocal responses change over time. |
| Facial Expression | visual | Cross-cultural facial emotion samples spanning 48 expression categories and varied lighting conditions. |
| Language | text | Text samples annotated for emotional expression, sentiment, and content safety across multiple languages. |

## Openly fetchable S3 media (verified live, 2026-08-26)

Found embedded in hume.ai/research page markup. Buckets are public-read per-object; bucket listing (ListBucket) denied → counts are page-capped at 500 per prefix, true totals likely higher.

| Prefix | Bucket | Count found | Format | Content (from filenames) |
|---|---|---|---|---|
| targaudio | mturkrecord | 500 | mp3 | sampled target audio: named speakers (Adam_009, Ana_056…), AnimeBursts emotion clips, CHI child vocalizations |
| targexps | mturkrecord | 500 | mp3 | expressive speech samples |
| targpros | mturkrecord | 500 | mp3 | prosody samples |
| testreactions | stimshare | 500 | mp4 | face-video reaction clips (PA-/VE- prefixed participant IDs) |
| — | stimshare | 1 | mp4 | greybar.mp4 (calibration/fixation stimulus) |
| **Total** | | **2001** | | |

Sample verified URLs (HTTP 200):
- https://mturkrecord.s3.amazonaws.com/targaudio/Adam_009.mp3
- https://stimshare.s3.us-east-2.amazonaws.com/testreactions/VE-22d95213-1617165611-2168loop8.mp4.mp4

## Key publications (2026)

1. **RW-Voice-EQ Bench: A Real World Benchmark for Evaluating Voice AI Systems** — arXiv, July 2026
2. **The 2026 ACII Dyadic Conversations (DaiKon) Workshop & Challenge** — May 2026
3. **TADA: A Generative Framework for Speech Modeling via Text-Acoustic Dual Alignment** — arXiv, February 2026

Other links of record:
- GitHub org: https://github.com/humeai
- Research portal: https://www.hume.ai/research
- Publications list: https://www.hume.ai/publications
