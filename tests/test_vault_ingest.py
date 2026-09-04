"""Tests for the vault ingestion pipeline v1 (HU-2699).

Light tests (always run): config/VLM gate, tier layout, router decisions,
metrics, and the Tier-0 PDF lane (pymupdf only).

Heavy regression smoke tests (run when docling / faster-whisper and the
experiment sample sets are available) pin the HU-2692/HU-2697 measured
baselines: PDF token-F1 per sample and whisper WER. On the CPU box these run
via the ingest extras environment::

    PYTHONPATH=src HF_HOME=experiments/ingestion-pdf/.models/hf \\
        experiments/ingestion-pdf/.venv/bin/python -m pytest tests/test_vault_ingest.py -v
"""

from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PDF_EXP = REPO_ROOT / "experiments" / "ingestion-pdf"
MEDIA_EXP = REPO_ROOT / "experiments" / "ingestion-media"

# docling resolves its model cache from HF_HOME; the experiment run already
# populated it (setdefault: never override an operator-provided value).
os.environ.setdefault("HF_HOME", str(PDF_EXP / ".models" / "hf"))

from huible.vault_ingest import (  # noqa: E402
    IngestConfig,
    VaultWriter,
    ingest_image,
    ingest_pdf,
)
from huible.vault_ingest.atoms import Tier  # noqa: E402
from huible.vault_ingest.config import VLM_ENABLED_ENV  # noqa: E402
from huible.vault_ingest.metrics import token_f1, wer  # noqa: E402
from huible.vault_ingest.pdf import route_page  # noqa: E402
from huible.vault_ingest.vlm import VLMDisabledError, vlm_page_pass  # noqa: E402


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


HAS_PYMUPDF = _has_module("pymupdf")
HAS_DOCLING = _has_module("docling")
HAS_WHISPER = _has_module("faster_whisper")
HAS_PDF_SAMPLES = (PDF_EXP / "samples" / "real_mixed.pdf").exists()
HAS_MEDIA_SAMPLES = (MEDIA_EXP / "samples" / "LibriSpeech").exists() and (
    MEDIA_EXP / "samples" / "bbb_720p.mp4"
).exists()


def _writer(tmp_path: pathlib.Path) -> VaultWriter:
    return VaultWriter(tmp_path / "ingest_out")


# ─── Config / VLM gate ────────────────────────────────────────────────────────


def test_vlm_disabled_by_default():
    config = IngestConfig.from_env({})
    assert config.vlm_enabled is False


def test_vlm_env_override(monkeypatch):
    monkeypatch.setenv(VLM_ENABLED_ENV, "1")
    assert IngestConfig.from_env().vlm_enabled is True
    monkeypatch.setenv(VLM_ENABLED_ENV, "garbage")
    assert IngestConfig.from_env().vlm_enabled is False


def test_vlm_page_pass_blocked_when_disabled(tmp_path):
    png = tmp_path / "page.png"
    png.write_bytes(b"\x89PNG fake")
    with pytest.raises(VLMDisabledError, match="disabled by config"):
        vlm_page_pass(str(png), IngestConfig())


# ─── Metrics (baseline scoring definitions) ──────────────────────────────────


def test_token_f1_matches_experiment_definition():
    assert token_f1("a b c", "a b c") == 1.0
    assert token_f1("", "a b") == 0.0
    # case-normalized; the measured definition strips most punctuation but
    # keeps . , / + - $ % as token characters (mirrors experiments score.py)
    assert token_f1("Hello World!", "hello world") == 1.0
    assert token_f1("hello,", "hello") < 1.0


def test_wer_matches_experiment_definition():
    assert wer("a b c", "a b c") == 0.0
    assert wer("a x c", "a b c") == pytest.approx(1 / 3)
    assert wer("", "a b") == 1.0


# ─── Atom tier layout ─────────────────────────────────────────────────────────


def test_writer_two_tier_layout(tmp_path):
    w = _writer(tmp_path)
    src = tmp_path / "orig.txt"
    src.write_text("irreplaceable raw measurement")
    stored = w.store_original(src)
    assert (tmp_path / "ingest_out" / stored["stored_as"]).exists()
    assert stored["stored_as"].startswith(f"{Tier.VAULT.value}/originals/")
    assert len(stored["sha256"]) == 64


# ─── Router (pymupdf text-layer check) ────────────────────────────────────────


@pytest.fixture
def tiny_pdf(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    p0 = doc.new_page()
    p0.insert_textbox(pymupdf.paper_rect("letter"), "native text layer " * 20, fontsize=11)
    doc.new_page()  # empty page: no text layer -> Tier 1
    path = tmp_path / "tiny.pdf"
    doc.save(path)
    doc.close()
    return path


@pytest.mark.skipif(not HAS_PYMUPDF, reason="pymupdf not installed")
def test_router_text_layer_decision(tiny_pdf):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open(tiny_pdf)
    assert route_page(doc[0]) == "tier0"
    assert route_page(doc[1]) == "tier1"
    doc.close()


# ─── Image lane ───────────────────────────────────────────────────────────────


def test_image_lane_stores_source_of_truth(tmp_path):
    png = tmp_path / "photo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    w = _writer(tmp_path)
    report = ingest_image(png, w)
    assert report["sha256"]
    (atom,) = w.atoms
    assert atom["atom_type"] == "image_source"
    assert atom["tier"] == Tier.VAULT.value
    assert "retrieval rides extracted text" in atom["provenance"]["retrieval"]
    assert "not the primary key" in atom["provenance"]["retrieval"]


# ─── Tier-0 PDF lane (light: pymupdf only) ───────────────────────────────────


@pytest.mark.skipif(
    not (HAS_PYMUPDF and HAS_PDF_SAMPLES), reason="pymupdf / PDF samples unavailable"
)
def test_tier0_native_pdf_matches_baseline(tmp_path):
    """real_mixed is all-native text: router must stay Tier 0 and reproduce the
    measured F1 1.0 baseline (HU-2692) with pymupdf alone."""
    sample = PDF_EXP / "samples" / "real_mixed.pdf"
    gt = (PDF_EXP / "ground_truth" / "real_mixed.pdf.gt.txt").read_text()
    w = _writer(tmp_path)
    report = ingest_pdf(sample, w)
    assert report["tier1_pages"] == 0
    assert report["tier0_pages"] == 15
    text = "\n".join(a["content"]["text"] for a in w.atoms if a["atom_type"] == "doc_page_text")
    assert token_f1(text, gt) >= 0.99


@pytest.mark.skipif(
    not (HAS_PYMUPDF and HAS_PDF_SAMPLES), reason="pymupdf / PDF samples unavailable"
)
def test_router_flags_images_for_vlm_pending(tmp_path):
    """chart_table has embedded images on a native-text page; with the VLM lane
    off, the page must still extract natively but be flagged vlm-pending."""
    sample = PDF_EXP / "samples" / "chart_table.pdf"
    w = _writer(tmp_path)
    report = ingest_pdf(sample, w)
    page = report["pages"][0]
    assert page["route"] == "tier0"
    assert "has_images_vlm_pending" in page["flags"]


# ─── Heavy regression smoke: full router + Tier 1 (docling CPU) ──────────────


PDF_F1_BASELINES = {
    # sample: (route expectation, min token-F1 vs ground truth)
    # measured HU-2692: real_mixed 1.000 (tier0), scanned_formula 0.931,
    # scanned_mixed 0.978 (tier1 docling), chart_table 0.394 (tier0, flat table)
    "real_mixed": ("tier0", 0.99),
    "scanned_formula": ("tier1", 0.90),
    "scanned_mixed": ("tier1", 0.93),
    "chart_table": ("tier0", 0.30),
}


def _extracted_text_for(out_root: pathlib.Path) -> str:
    parts = []
    atoms_dir = out_root / "vault" / "atoms"
    for f in sorted(atoms_dir.glob("*.doc_page_*.json")):
        import json

        atom = json.loads(f.read_text())
        content = atom["content"]
        parts.append(content.get("text") or content.get("markdown") or "")
    return "\n".join(parts)


@pytest.mark.skipif(
    not (HAS_DOCLING and HAS_PYMUPDF and HAS_PDF_SAMPLES),
    reason="docling/pymupdf or PDF samples unavailable",
)
@pytest.mark.parametrize("name", sorted(PDF_F1_BASELINES))
def test_pdf_regression_baselines(tmp_path, name):
    expected_route, min_f1 = PDF_F1_BASELINES[name]
    sample = PDF_EXP / "samples" / f"{name}.pdf"
    gt = (PDF_EXP / "ground_truth" / f"{name}.pdf.gt.txt").read_text()
    out = tmp_path / "ingest_out"
    w = VaultWriter(out)
    report = ingest_pdf(sample, w)

    for page in report["pages"]:
        if expected_route == "tier0":
            assert page["route"] == "tier0"
        # scanned samples are single-page; their only page must route Tier 1
        else:
            assert page["route"] == "tier1"

    f1 = token_f1(_extracted_text_for(out), gt)
    assert f1 >= min_f1, f"{name}: token-F1 {f1:.3f} below pinned baseline {min_f1}"


@pytest.mark.skipif(
    not (HAS_DOCLING and HAS_PYMUPDF and HAS_PDF_SAMPLES),
    reason="docling/pymupdf or PDF samples unavailable",
)
def test_tier2_vlm_off_by_default_and_formula_pages_flagged(tmp_path):
    """Acceptance: VLM Tier-2 flag verified OFF by default; scanned formula page
    retains its image in the vault and records the skip reason."""
    sample = PDF_EXP / "samples" / "scanned_formula.pdf"
    out = tmp_path / "ingest_out"
    w = VaultWriter(out)
    report = ingest_pdf(sample, w)

    page = report["pages"][0]
    assert page["vlm"]["status"] == "skipped"
    assert "spend approval" in page["vlm"]["reason"]
    assert any("formula-not-decoded" in f for f in page["flags"])
    assert any(f == "page_image_retained_vault" for f in page["flags"])
    assert (out / "vault" / "page_png").is_dir()
    # and the flag is genuinely config-driven: no VLM atom can exist while off
    assert not list((out / "vault" / "atoms").glob("*doc_page_vlm*"))


# ─── Heavy regression smoke: audio lane (faster-whisper CPU) ─────────────────


def _manifest():
    import json

    return json.loads((MEDIA_EXP / "ground_truth" / "manifest.json").read_text())


@pytest.mark.skipif(
    not (HAS_WHISPER and HAS_MEDIA_SAMPLES), reason="faster-whisper / media samples unavailable"
)
def test_audio_lane_wer_baseline(tmp_path):
    """Pin the HU-2697 measured baseline: base.en int8 file WER on a
    LibriSpeech test-clean utterance (measured 0.1176 for this file)."""
    entry = _manifest()["entries"][0]
    audio = MEDIA_EXP / "samples" / entry["audio"]
    w = _writer(tmp_path)

    from huible.vault_ingest import ingest_audio

    report = ingest_audio(audio, w, IngestConfig(), reference_text=entry["reference_text"])

    assert report["file_wer_vs_reference"] <= 0.35
    (atom,) = [a for a in w.atoms if a["atom_type"] == "dialog_verbatim"]
    assert atom["tier"] == Tier.VAULT.value
    assert atom["provenance"]["model"] == "base.en"
    assert atom["provenance"]["compute"] == "cpu/int8"
    assert atom["provenance"]["wer_context"]["corpus_wer"] == pytest.approx(0.077, abs=0.005)
    seg = atom["content"]["segments"][0]
    assert {"start", "end", "text", "no_speech_prob"} <= set(seg)
    assert "vad_gating_followup" in atom["content"]["flags"]


@pytest.mark.skipif(
    not (HAS_WHISPER and HAS_MEDIA_SAMPLES), reason="faster-whisper / media samples unavailable"
)
def test_audio_corpus_wer_baseline(tmp_path):
    """Corpus-level pin over the full 32-utterance manifest:
    measured base.en corpus WER 0.0773 (HU-2697)."""
    from huible.vault_ingest import ingest_audio

    manifest = _manifest()
    hyps, refs = [], []
    for entry in manifest["entries"]:
        w = _writer(tmp_path)
        ingest_audio(
            MEDIA_EXP / "samples" / entry["audio"],
            w,
            IngestConfig(),
            reference_text=entry["reference_text"],
        )
        atom = next(a for a in w.atoms if a["atom_type"] == "dialog_verbatim")
        hyps.append(atom["content"]["verbatim_text"])
        refs.append(entry["reference_text"])
    assert wer(" ".join(hyps), " ".join(refs)) <= 0.09


# ─── Heavy smoke: video lane (ffmpeg composition) ────────────────────────────


@pytest.mark.skipif(
    not (HAS_WHISPER and HAS_MEDIA_SAMPLES), reason="faster-whisper / media samples unavailable"
)
def test_video_lane_composition(tmp_path):
    """Video = audio track -> whisper lane (vault), 1 fps frames -> regenerable
    intermediates (derived). BBB soundtrack is music/sfx: transcript must carry
    the non-dialog caution flags (HU-2697 hallucination failure mode)."""
    from huible.vault_ingest import ingest_video

    video = MEDIA_EXP / "samples" / "bbb_720p.mp4"
    out = tmp_path / "ingest_out"
    w = VaultWriter(out)
    report = ingest_video(video, w, IngestConfig())

    assert report["probe"]["duration_sec"] > 0
    assert (out / "derived" / "media" / "bbb_720p_16k.wav").exists()
    frames_dir = out / "derived" / "media" / "bbb_720p_frames"
    assert len(list(frames_dir.glob("frame_*.jpg"))) == report["frame_sampling"]["frames"] > 0
    # original in vault; frames atom is derived-tier (regenerable)
    assert any(f.name.endswith(".mp4") for f in (out / "vault" / "originals").iterdir())
    (frames_atom,) = [a for a in w.atoms if a["atom_type"] == "media_frames"]
    assert frames_atom["tier"] == Tier.DERIVED.value
    (dialog,) = [a for a in w.atoms if a["atom_type"] == "dialog_verbatim"]
    assert dialog["tier"] == Tier.VAULT.value
    assert "no_speech_prob_stored" in dialog["content"]["flags"]
    assert "vad_gating_followup" in dialog["content"]["flags"]


# ─── Dispatcher ───────────────────────────────────────────────────────────────


def test_dispatcher_rejects_unknown_type(tmp_path):
    from huible.vault_ingest import ingest_path

    weird = tmp_path / "file.xyz"
    weird.write_text("data")
    with pytest.raises(ValueError, match="unsupported input type"):
        ingest_path(weird, _writer(tmp_path))
