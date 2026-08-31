# R&D Validation: Persona Vault Buildout (HU-2309)

## 1. Open Questions Resolved

**1. Atom schema for media (clip-atom fields)**
Aligned with HU-1839 S2/S3 typed units. The media schema treats clips and photos as immutable atoms with provenance:
```json
{
  "unit_type": "media-clip-atom",
  "id": "uuid",
  "persona_id": "string",
  "modality": "audio|video|photo",
  "source_ref": {
    "url": "string",
    "offset_ms": "integer",
    "duration_ms": "integer"
  },
  "measurements": {
    "diarization_label": "string",
    "quality_score": "float"
  },
  "provenance": {
    "license": "string",
    "consent_status": "string"
  }
}
```

**2. Presence layer source for Chandler**
*Decision:* Use episode air-time patterns and scene appearance density as a proxy for activity rhythms. No waiver needed; this provides a strong test case for deriving presence from structured episodic data rather than real-time messaging logs.

**3. Essence-fidelity eval design**
*Protocol:* Blind A/B indistinguishability test (BEAM-style).
* Method: Generate 50 novel responses using the Chandler persona to prompts drawn from unseen episodes/situations. Mix with 50 real Chandler responses.
* Task: Blind raters must classify each response as "Real Human" or "Generated Persona".
* Pass Bar: The persona passes if raters cannot distinguish better than random chance (accuracy < 60%) or if the False Positive rate (classifying generation as real) exceeds 40%.

**4. 06-presence residency rule**
*Decision:* Confirming the default. The **raw measurements** (extracted activity timestamps, response latencies derived from the source) live in the vault as irreplaceable ground truth. The **derived rhythm stats** (aggregated probability distributions, Markov chains for sleep/wake states) live in TencentDB, as they can be regenerated from the vault's raw measurements.

**5. MELD clips for 04-voice reference bank**
*Decision:* Acceptable for eval-grade reference banking. Per the Aug 27 rights & licensing red lines, celebrity reference media for an internal demo falls under research/eval use. We are building the reference bank, not training a public-facing generator, so this complies with the media doctrine.

## 2. Integration Spikes (HU-1839)
The V1 Ingest stage will hook into HU-1839's S0-S5 pipeline. Specifically:
* **S2/S3 Rules:** We will extend the S3 turn-atom rules to enforce the Verbatim-grounding gate (every atom MUST map to a source offset).
* **Media handling:** HU-1839 will need typed parsers for audio/video to emit the `media-clip-atom` format described above.

## 3. Next Steps
The design is validated by R&D. Requesting Pat's sign-off to freeze this plan so the Librarian can create the official vault doc and the Tech Lead can begin Kestra flow implementation.
