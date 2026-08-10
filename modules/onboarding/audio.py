#!/usr/bin/env python3
"""
Huible Onboarding — Stage 1b: AUDIO INGESTION (multimodal)

Ingests per-utterance acoustic/prosodic feature vectors (OpenSMILE / MFCC /
librosa) for the target persona and aggregates them into a persona-level vocal
profile that the L0-L3 distillation pipeline and the OKF structuring stage can
ground persona traits in vocal/prosodic evidence.

Supported input formats (auto-detected):
  1. MELD-style audio features CSV — per-utterance rows with at least a
     ``Speaker`` column (and typically ``Dialogue_ID`` / ``Utterance_ID`` /
     ``Emotion`` / ``Text``) followed by OpenSMILE/MFCC numeric feature columns.
     This is the native shape of the MELD audio features release
     (``MELD.AudioFeatures.<split>.csv``).
  2. Generic per-utterance audio JSONL — one JSON object per line with at least
     ``speaker`` and an ``acoustic`` dict (``pitch``/``intensity``/``emotion``
     /``emotion_vector``/``mfcc`` keys).

The persona filter uses the ``Speaker`` / ``speaker`` field. When a cleaned
dialog JSONL (``--cleaned``) is provided, per-utterance features are joined to
the dialog text by normalized text match so structure.py can cite prosodic
evidence alongside the quote.

Outputs:
  --output <audio_features.json>
      Persona-level vocal profile + the filtered per-utterance acoustic
      records. Downstream stages (distillation, structure) consume this.

This stage is deterministic and dependency-free (stdlib only): it never calls
an LLM and never invents features. When no audio data is present the pipeline
simply skips this stage (see flows/onboard.yaml).

Usage:
  python3 audio.py \\
      --features /input/MELD.AudioFeatures.train.csv \\
      --persona chandler \\
      --cleaned /tmp/onboarding/chandler/cleaned.jsonl \\
      --output  /tmp/onboarding/chandler/audio_features.json

  # Generic per-utterance JSONL instead of MELD CSV:
  python3 audio.py \\
      --features /input/per_utterance_audio.jsonl \\
      --persona chandler \\
      --output  /tmp/onboarding/chandler/audio_features.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

# Feature columns that are metadata, not acoustic measurements. Everything else
# numeric becomes part of the per-utterance feature vector.
_META_COLUMNS = {
    "sr no", "sr_no", "srno",
    "dialogue_id", "dialogueid", "dialog_id",
    "utterance_id", "utteranceid", "utt_id",
    "speaker", "emotion", "sentiment",
    "text", "utterance", "transcript", "season", "episode",
}

# Canonical MELD emotion labels.
_EMOTION_LABELS = {
    "neutral", "joy", "sadness", "anger", "fear", "surprise", "disgust",
    "non-neutral", "excited", "frustrated", "happy", "sad", "calm",
}

# Acoustic facets structure.py / distillation look for. Pitch (F0) and
# intensity columns are matched case-insensitively against these stems so the
# module tolerates the column naming variance across OpenSMILE feature sets
# (e.g. ``F0mean``, ``F0_sma``, ``pcm_fftMag_fbinProcCentroid``).
_PITCH_STEMS = ("f0", "pitch", "pcm_fftmag_fbinproccentroid")
_INTENSITY_STEMS = ("intensity", "rms", "pcm_fftmag_spectralCentroid")
_MFCC_STEMS = ("mfcc",)
# Spread columns describe variability, not the value itself — exclude them
# from the per-utterance pitch/intensity value aggregation.
_SPREAD_STEMS = ("std", "range", "min", "max", "dev", "spread", "variance")


def _is_value_column(name: str, stems: tuple[str, ...]) -> bool:
    """True when ``name`` matches a value stem but is not a spread descriptor."""
    lname = name.lower()
    if not any(s in lname for s in stems):
        return False
    return not any(s in lname for s in _SPREAD_STEMS)


def _is_meta_column(name: str) -> bool:
    """True for metadata columns (ids, speaker, emotion, text, ...).

    Comparison is robust to trailing punctuation/whitespace so column headers
    like ``Sr No.`` are treated as metadata regardless of exact spelling.
    """
    lname = name.lower().strip()
    # Collapse to alphanumerics + underscore for the membership test.
    compact = lname.replace(" ", "").replace(".", "").replace("_", "")
    meta_compact = {m.replace(" ", "").replace(".", "").replace("_", "") for m in _META_COLUMNS}
    return compact in meta_compact


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------

def _norm_text(text: str) -> str:
    return " ".join((text or "").lower().strip().split())


def _norm_speaker(name: str) -> str:
    return (name or "").strip().lower()


def _to_float(value: Any) -> float | None:
    """Parse a numeric cell. Returns None for blanks and non-numeric strings."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _detect_features_path(features_arg: str) -> Path | None:
    """Resolve --features to a single file (dir → first MELD csv found)."""
    p = Path(features_arg)
    if p.is_dir():
        candidates = sorted(
            list(p.glob("*.csv")) + list(p.glob("*.jsonl"))
        )
        # Prefer MELD-named files when present.
        for c in candidates:
            if "audio" in c.name.lower() or "meld" in c.name.lower():
                return c
        return candidates[0] if candidates else None
    if p.is_file():
        return p
    return None


# --------------------------------------------------------------------------
# MELD CSV ingestion
# --------------------------------------------------------------------------

def ingest_meld_csv(filepath: Path, persona: str) -> list[dict[str, Any]]:
    """Read a MELD audio features CSV → per-utterance acoustic records.

    Each returned record has:
      speaker, text (optional), emotion (optional),
      utt_id (when Dialogue_ID / Utterance_ID present),
      acoustic: {pitch, intensity, emotion_vector, mfcc_mean, features: {...}}
    Only rows whose Speaker matches the persona are returned.
    """
    records: list[dict[str, Any]] = []
    with open(filepath, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return records
        header = [h.strip() for h in header]
        lower = [h.lower() for h in header]

        speaker_idx = _find(lower, "speaker")
        text_idx = _find_any(lower, ("text", "utterance", "transcript"))
        emotion_idx = _find_any(lower, ("emotion", "sentiment"))
        dialog_idx = _find_any(lower, ("dialogue_id", "dialog_id", "dialogueid"))
        utt_idx = _find_any(lower, ("utterance_id", "utteranceid", "utt_id"))

        # Numeric feature columns = everything not metadata.
        feature_cols: list[tuple[int, str]] = [
            (i, header[i])
            for i, h in enumerate(lower)
            if h != "" and not _is_meta_column(h)
        ]

        if speaker_idx is None:
            print(
                "[audio] WARNING: no 'Speaker' column in CSV; cannot persona-filter.",
                file=sys.stderr,
            )

        for row in reader:
            if not row:
                continue
            speaker = _norm_speaker(row[speaker_idx]) if speaker_idx is not None else ""
            if speaker_idx is not None and persona and speaker != persona.lower():
                continue

            features: dict[str, float] = {}
            pitch_vals: list[float] = []
            intensity_vals: list[float] = []
            mfcc_vals: list[float] = []
            for idx, name in feature_cols:
                if idx >= len(row):
                    continue
                val = _to_float(row[idx])
                if val is None:
                    continue
                features[name] = val
                if _is_value_column(name, _PITCH_STEMS):
                    pitch_vals.append(val)
                if _is_value_column(name, _INTENSITY_STEMS):
                    intensity_vals.append(val)
                if any(s in name.lower() for s in _MFCC_STEMS):
                    mfcc_vals.append(val)

            text = row[text_idx].strip() if text_idx is not None and text_idx < len(row) else ""
            emotion = (
                row[emotion_idx].strip().lower()
                if emotion_idx is not None and emotion_idx < len(row)
                else ""
            )

            utt_id = ""
            if (
                dialog_idx is not None and dialog_idx < len(row)
                and utt_idx is not None and utt_idx < len(row)
            ):
                utt_id = f"dia{row[dialog_idx].strip()}_utt{row[utt_idx].strip()}"

            records.append(
                _build_record(
                    speaker=speaker or persona.lower(),
                    text=text,
                    emotion=emotion,
                    utt_id=utt_id,
                    pitch_vals=pitch_vals,
                    intensity_vals=intensity_vals,
                    mfcc_vals=mfcc_vals,
                    features=features,
                )
            )
    return records


def _find(header_lower: list[str], name: str) -> int | None:
    for i, h in enumerate(header_lower):
        if h == name:
            return i
    return None


def _find_any(header_lower: list[str], names: tuple[str, ...]) -> int | None:
    for i, h in enumerate(header_lower):
        if h in names:
            return i
    return None


# --------------------------------------------------------------------------
# Generic per-utterance JSONL ingestion
# --------------------------------------------------------------------------

def ingest_audio_jsonl(filepath: Path, persona: str) -> list[dict[str, Any]]:
    """Read a generic per-utterance audio JSONL → acoustic records.

    Each line must have at least ``speaker`` and an ``acoustic`` dict. The
    ``acoustic`` dict accepts: pitch, intensity, emotion (label), emotion_vector
    (list/dict), mfcc (list/dict). A ``text`` and ``utt_id`` field are optional.
    """
    records: list[dict[str, Any]] = []
    with open(filepath, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            speaker = _norm_speaker(entry.get("speaker", ""))
            if persona and speaker != persona.lower():
                continue
            ac = entry.get("acoustic") or {}
            records.append(
                _build_record(
                    speaker=speaker or persona.lower(),
                    text=str(entry.get("text", "")),
                    emotion=str(ac.get("emotion", "") or entry.get("emotion", "")),
                    utt_id=str(entry.get("utt_id", "")),
                    pitch_vals=_as_float_list(ac.get("pitch")),
                    intensity_vals=_as_float_list(ac.get("intensity")),
                    mfcc_vals=_as_float_list(_flatten(ac.get("mfcc"))),
                    features=_flatten_numeric(ac),
                )
            )
    return records


def _as_float_list(value: Any) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, list):
        return [v for v in (_to_float(x) for x in value) if v is not None]
    v = _to_float(value)
    return [v] if v is not None else []


def _flatten(value: Any) -> list[float]:
    """Flatten nested list/dict MFCC payloads into a flat float list."""
    out: list[float] = []
    if isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten(v))
    elif isinstance(value, list):
        for item in value:
            out.extend(_flatten(item))
    else:
        f = _to_float(value)
        if f is not None:
            out.append(f)
    return out


def _flatten_numeric(d: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {}
    for k, v in d.items():
        if isinstance(v, (int, float)):
            flat[str(k)] = float(v)
    return flat


# --------------------------------------------------------------------------
# Record assembly + aggregation
# --------------------------------------------------------------------------

def _build_record(
    *,
    speaker: str,
    text: str,
    emotion: str,
    utt_id: str,
    pitch_vals: list[float],
    intensity_vals: list[float],
    mfcc_vals: list[float],
    features: dict[str, float],
) -> dict[str, Any]:
    acoustic: dict[str, Any] = {}
    if pitch_vals:
        acoustic["pitch"] = _summarize(pitch_vals)
    if intensity_vals:
        acoustic["intensity"] = _summarize(intensity_vals)
    if mfcc_vals:
        acoustic["mfcc_mean"] = round(statistics.fmean(mfcc_vals), 4)
    if emotion:
        acoustic["emotion"] = emotion
    if features:
        # Keep a bounded feature sample (full vectors stay in the source CSV).
        acoustic["features"] = dict(sorted(features.items())[:12])
    return {
        "speaker": speaker,
        "text": text,
        "utt_id": utt_id,
        "acoustic": acoustic,
        "emotion": emotion if emotion in _EMOTION_LABELS else (emotion or None),
    }


def _summarize(vals: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(vals), 4),
        "std": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
    }


def aggregate_persona_profile(records: list[dict[str, Any]], persona: str) -> dict[str, Any]:
    """Roll per-utterance acoustic records up into a persona-level vocal profile."""
    n = len(records)
    if n == 0:
        return {
            "persona": persona,
            "utterance_count": 0,
            "available": False,
            "prosody_summary": "No acoustic data available.",
        }

    pitches: list[float] = []
    intensities: list[float] = []
    mfccs: list[float] = []
    emotion_counts: dict[str, int] = {}
    for rec in records:
        ac = rec.get("acoustic", {})
        if "pitch" in ac:
            pitches.append(ac["pitch"]["mean"])
        if "intensity" in ac:
            intensities.append(ac["intensity"]["mean"])
        if "mfcc_mean" in ac:
            mfccs.append(ac["mfcc_mean"])
        emo = rec.get("emotion")
        if emo:
            emotion_counts[emo] = emotion_counts.get(emo, 0) + 1

    total_emo = sum(emotion_counts.values()) or 1
    emotion_distribution = {
        e: round(c / total_emo, 4) for e, c in sorted(
            emotion_counts.items(), key=lambda kv: kv[1], reverse=True
        )
    }

    profile: dict[str, Any] = {
        "persona": persona,
        "utterance_count": n,
        "available": True,
        "pitch": _summarize(pitches) if pitches else None,
        "intensity": _summarize(intensities) if intensities else None,
        "mfcc_mean_overall": round(statistics.fmean(mfccs), 4) if mfccs else None,
        "emotion_distribution": emotion_distribution,
        "dominant_emotion": max(emotion_counts, key=emotion_counts.get) if emotion_counts else None,
    }
    profile["prosody_summary"] = _prosody_summary(profile)
    return profile


def _prosody_summary(profile: dict[str, Any]) -> str:
    """Render a compact one-line prosody summary for the structuring prompt."""
    if not profile.get("available"):
        return "No acoustic data available."
    parts: list[str] = []
    pitch = profile.get("pitch")
    if pitch:
        parts.append(
            f"mean pitch {pitch['mean']:.1f} Hz (±{pitch['std']:.1f}, "
            f"range {pitch['min']:.0f}-{pitch['max']:.0f})"
        )
    intensity = profile.get("intensity")
    if intensity:
        parts.append(f"intensity mean {intensity['mean']:.3f} (±{intensity['std']:.3f})")
    dom = profile.get("dominant_emotion")
    dist = profile.get("emotion_distribution") or {}
    if dom:
        pct = round(dist.get(dom, 0) * 100)
        parts.append(f"dominant vocal emotion {dom} ({pct}%)")
    if not parts:
        return "Acoustic features present but pitch/intensity/emotion not extractable."
    return "; ".join(parts) + "."


# --------------------------------------------------------------------------
# Cleaned-dialog join (text ↔ acoustic)
# --------------------------------------------------------------------------

def join_cleaned(
    records: list[dict[str, Any]], cleaned_path: str | None
) -> list[dict[str, Any]]:
    """Attach dialog text to acoustic records by normalized text match.

    MELD CSVs already carry a Text column; this join only fills in text for
    records missing it (e.g. JSONL inputs without text) using the cleaned
    dialog corpus. Returns the records unchanged if no cleaned path is given.
    """
    if not cleaned_path or not os.path.exists(cleaned_path):
        return records
    by_norm: dict[str, str] = {}
    with open(cleaned_path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = (entry.get("text") or "").strip()
            if t:
                by_norm[_norm_text(t)] = t
    if not by_norm:
        return records
    for rec in records:
        if rec.get("text"):
            continue
        key = _norm_text(rec.get("text", "")) or _norm_text(rec.get("utt_id", ""))
        match = by_norm.get(key)
        if match:
            rec["text"] = match
    return records


def build_acoustic_index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index per-utterance acoustic features by normalized text for fast join."""
    index: dict[str, dict[str, Any]] = {}
    for rec in records:
        t = rec.get("text")
        if not t:
            continue
        key = _norm_text(t)
        if key and key not in index and rec.get("acoustic"):
            index[key] = rec["acoustic"]
    return index


def augment_cleaned_jsonl(
    cleaned_path: str, acoustic_index: dict[str, dict[str, Any]], out_path: str
) -> int:
    """Write a copy of cleaned.jsonl with an ``acoustic`` field joined per line.

    Lines whose text matches a per-utterance acoustic record get that record's
    ``acoustic`` dict; unmatched lines pass through unchanged. Returns the
    number of matched lines.
    """
    matched = 0
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(cleaned_path, encoding="utf-8") as fin, \
         open(out_path, "w", encoding="utf-8") as fout:
        for raw in fin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            key = _norm_text(entry.get("text", ""))
            ac = acoustic_index.get(key)
            if ac:
                entry["acoustic"] = ac
                matched += 1
            fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return matched


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

def run(
    features_path: str,
    persona: str,
    cleaned: str | None,
    output: str,
    augment_output: str | None = None,
) -> dict[str, Any]:
    """Execute audio ingestion → persona-level audio_features.json.

    When ``augment_output`` is given (and ``cleaned`` is available), also writes
    an augmented copy of the cleaned JSONL with per-line ``acoustic`` features
    so the distillation stage can carry them into L0Record metadata.
    Returns the artifact dict.
    """
    resolved = _detect_features_path(features_path)
    if resolved is None:
        raise FileNotFoundError(f"No audio features found at {features_path}")

    if resolved.suffix == ".csv":
        records = ingest_meld_csv(resolved, persona)
        source_kind = "meld_csv"
    else:
        records = ingest_audio_jsonl(resolved, persona)
        source_kind = "audio_jsonl"

    records = join_cleaned(records, cleaned)
    profile = aggregate_persona_profile(records, persona)

    artifact: dict[str, Any] = {
        "persona": persona,
        "source": str(resolved),
        "source_kind": source_kind,
        "utterance_count": len(records),
        "persona_profile": profile,
        "per_utterance": records,
    }

    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, ensure_ascii=False)

    if augment_output and cleaned and os.path.exists(cleaned):
        acoustic_index = build_acoustic_index(records)
        matched = augment_cleaned_jsonl(cleaned, acoustic_index, augment_output)
        artifact["augmented_path"] = augment_output
        artifact["augmented_matched"] = matched

    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest per-utterance acoustic features into a persona vocal profile."
    )
    parser.add_argument(
        "--features", required=True,
        help="MELD audio features CSV, generic per-utterance audio JSONL, "
             "or a directory containing one.",
    )
    parser.add_argument("--persona", default="chandler", help="Target persona name")
    parser.add_argument(
        "--cleaned", help="Optional cleaned.jsonl to join text ↔ acoustic features."
    )
    parser.add_argument(
        "--augment-output",
        help="Optional path to write cleaned.jsonl augmented with per-line acoustic features.",
    )
    parser.add_argument("--output", required=True, help="Output audio_features.json path")
    args = parser.parse_args()

    try:
        artifact = run(args.features, args.persona, args.cleaned, args.output, args.augment_output)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    profile = artifact["persona_profile"]
    print(f"Ingested {artifact['utterance_count']} utterances for '{args.persona}' "
          f"({artifact['source_kind']}).")
    if profile.get("available"):
        print(f"  Prosody: {profile['prosody_summary']}")
        dist = profile.get("emotion_distribution") or {}
        top = ", ".join(f"{e}={p}" for e, p in list(dist.items())[:4])
        print(f"  Emotion mix: {top or 'n/a'}")
    else:
        print("  No persona utterances matched.")
    if artifact.get("augmented_path"):
        print(f"  Augmented cleaned dialog: {artifact['augmented_path']} "
              f"({artifact['augmented_matched']} matched)")

    result = {
        "persona": args.persona,
        "utterance_count": artifact["utterance_count"],
        "source_kind": artifact["source_kind"],
        "available": profile.get("available", False),
        "prosody_summary": profile.get("prosody_summary"),
        "augmented_matched": artifact.get("augmented_matched"),
    }
    print(f'::{json.dumps({"outputs": result})}::')


if __name__ == "__main__":
    main()
