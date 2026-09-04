"""Regression gate for ``scripts/build_human_goldset.py`` (HU-2157 item 2).

The human gold-set path is the ONLY route to gate promotion (production_safe)
— the builder must fail closed on missing consent, reject unusable/multi-face
photos, dedupe, and lay out exactly what calibrate_gate.py expects. Faces are
monkeypatched (no insightface models in CI); images are real tiny PNGs so
phash works.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "build_human_goldset.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_build_human_goldset", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _png(path: Path, seed: int = 0) -> Path:
    """Real 640x640 image with structure that varies by seed (distinct phash)."""
    from PIL import ImageDraw
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (640, 640), color=(200, 120, 90))
    d = ImageDraw.Draw(img)
    d.rectangle([seed * 40 % 400 + 20, seed * 70 % 400 + 20,
                 seed * 40 % 400 + 220, seed * 70 % 400 + 220],
                fill=(20, 20, 20))
    img.save(path)
    return path


def _write_consent(path: Path, rows):
    lines = ["person,file,basis,consent_by,license_ref"]
    for r in rows:
        lines.append(",".join(r))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


GOOD_METRICS = {"faces": 1, "face_w_px": 300, "face_area_frac": 0.25,
                "yaw": 5.0, "pitch": 3.0, "roll": 0.0}


@pytest.fixture()
def mod():
    return _load_module()


def test_happy_path_layout_and_pair_math(mod, tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "gold"
    emb_a = [np.ones(512, dtype=np.float32) for _ in range(3)]
    emb_b = [-np.ones(512, dtype=np.float32) for _ in range(2)]
    for i, p in enumerate(("alice-1", "alice-2", "alice-3")):
        _png(src / "alice" / f"{p}.jpg", seed=i)
    _png(src / "bob" / "bob-1.jpg", seed=7)
    _png(src / "bob" / "bob-2.jpg", seed=8)
    _write_consent(tmp_path / "consent.csv", [
        ("alice", "alice-1.jpg", "client_upload", "client:alice:onboarding", ""),
        ("alice", "alice-2.jpg", "client_upload", "client:alice:onboarding", ""),
        ("alice", "alice-3.jpg", "client_upload", "client:alice:onboarding", ""),
        ("bob", "bob-1.jpg", "client_upload", "client:bob:onboarding", ""),
        ("bob", "bob-2.jpg", "client_upload", "client:bob:onboarding", ""),
    ])

    seq = {"alice": list(emb_a), "bob": list(emb_b)}

    def _embed(path):
        return seq[Path(path).parent.name].pop(0), dict(GOOD_METRICS)

    monkeypatch.setattr(mod, "_embed", _embed)

    # exact-copy duplicate of alice-2 must phash-dedupe away
    (src / "alice" / "alice-2.jpg").replace(src / "alice" / "tmp.jpg")
    _png(src / "alice" / "alice-2.jpg", seed=1)
    import shutil as _sh
    _sh.copyfile(src / "alice" / "alice-2.jpg", src / "alice" / "alice-4.jpg")
    _write_consent(tmp_path / "consent.csv", [
        ("alice", "alice-1.jpg", "client_upload", "client:alice:onboarding", ""),
        ("alice", "alice-2.jpg", "client_upload", "client:alice:onboarding", ""),
        ("alice", "alice-3.jpg", "client_upload", "client:alice:onboarding", ""),
        ("alice", "alice-4.jpg", "client_upload", "client:alice:onboarding", ""),
        ("bob", "bob-1.jpg", "client_upload", "client:bob:onboarding", ""),
        ("bob", "bob-2.jpg", "client_upload", "client:bob:onboarding", ""),
    ])
    seq["alice"] = emb_a + [emb_a[1]]  # one more embedding for the extra photo

    kept, rejected = mod.build(src, mod.load_consent(tmp_path / "consent.csv"), str(out))

    assert {k["person"] for k in kept} == {"alice", "bob"}
    alice = next(k for k in kept if k["person"] == "alice")
    bob = next(k for k in kept if k["person"] == "bob")
    assert (out / "alice" / "reference.png").is_file()
    assert (out / "bob" / "reference.png").is_file()
    assert len(list((out / "alice" / "outputs").iterdir())) == alice["n_outputs"] == 2
    assert bob["n_outputs"] == 1
    dupes = [r for r in rejected if r["reason"].startswith("duplicate_of:")]
    assert dupes and dupes[0]["file"] == "alice-4.jpg"

    pos, neg = mod.dry_scores(kept)
    # alice: 2 outputs -> 2 pos vs bob ref; bob: 1 output -> 1 pos vs alice ref
    assert len(pos) == 3
    assert len(neg) == 3
    assert min(pos) > max(neg)  # orthogonal embeddings separate cleanly


def test_missing_consent_row_rejected_fail_closed(mod, tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "gold"
    _png(src / "carol" / "c1.jpg")
    _png(src / "carol" / "c2.jpg")
    _write_consent(tmp_path / "consent.csv", [
        ("carol", "c1.jpg", "client_upload", "client:carol:onboarding", ""),
        # c2.jpg deliberately missing
    ])
    monkeypatch.setattr(
        mod, "_embed",
        lambda p: (np.ones(512, dtype=np.float32), dict(GOOD_METRICS)))

    kept, rejected = mod.build(src, mod.load_consent(tmp_path / "consent.csv"), str(out))

    assert len(kept) == 1 and kept[0]["n_outputs"] == 0
    assert {"person": "carol", "file": "c2.jpg", "reason": "no_consent_row"} \
        in [r for r in rejected]


def test_bad_basis_rejected(mod, tmp_path, monkeypatch):
    src = tmp_path / "src"
    _png(src / "dave" / "d1.jpg")
    _write_consent(tmp_path / "consent.csv", [
        ("dave", "d1.jpg", "web_scrape", "", ""),
    ])
    monkeypatch.setattr(
        mod, "_embed",
        lambda p: (np.ones(512, dtype=np.float32), dict(GOOD_METRICS)))

    kept, rejected = mod.build(src, mod.load_consent(tmp_path / "consent.csv"),
                               str(tmp_path / "gold"))
    assert kept == []
    assert rejected[0]["reason"] == "bad_basis:web_scrape"


def test_multi_face_and_quality_rejections(mod, tmp_path, monkeypatch):
    src = tmp_path / "src"
    _png(src / "erin" / "e1.jpg")
    _png(src / "erin" / "e2.jpg")
    _write_consent(tmp_path / "consent.csv", [
        ("erin", "e1.jpg", "client_upload", "client:erin:onboarding", ""),
        ("erin", "e2.jpg", "client_upload", "client:erin:onboarding", ""),
    ])
    metrics = {
        "e1": {"faces": 2, "face_w_px": 300, "face_area_frac": 0.25,
               "yaw": 0.0, "pitch": 0.0, "roll": 0.0},   # multi_face
        "e2": {"faces": 1, "face_w_px": 100, "face_area_frac": 0.25,
               "yaw": 0.0, "pitch": 0.0, "roll": 0.0},   # face_too_small
    }

    def _embed(path):
        name = Path(path).stem
        return np.ones(512, dtype=np.float32), dict(metrics[name])

    monkeypatch.setattr(mod, "_embed", _embed)
    kept, rejected = mod.build(src, mod.load_consent(tmp_path / "consent.csv"),
                               str(tmp_path / "gold"))
    assert kept == []
    reasons = {r["file"]: r["reason"] for r in rejected}
    assert reasons["e1.jpg"] == "multi_face"
    assert reasons["e2.jpg"] == "face_too_small"


def test_dry_scores_summary_end_to_end(mod, tmp_path, monkeypatch, capsys):
    """main() writes goldset-summary.json (no embeddings leaked) and reports pairs."""
    src, out = tmp_path / "src", tmp_path / "gold"
    for i, p in enumerate(("frank-1", "frank-2", "gina-1", "gina-2")):
        _png(src / p.split("-")[0] / f"{p}.jpg", seed=i)
    _write_consent(tmp_path / "consent.csv", [
        ("frank", "frank-1.jpg", "client_upload", "client:frank:onboarding", ""),
        ("frank", "frank-2.jpg", "client_upload", "client:frank:onboarding", ""),
        ("gina", "gina-1.jpg", "license", "", "stock#9"),
        ("gina", "gina-2.jpg", "client_upload", "client:gina:onboarding", ""),
    ])
    seq = {"frank": [np.ones(512, dtype=np.float32) for _ in range(2)],
           "gina": [-np.ones(512, dtype=np.float32) for _ in range(2)]}

    def _embed(path):
        return seq[Path(path).parent.name].pop(0), dict(GOOD_METRICS)

    monkeypatch.setattr(mod, "_embed", _embed)
    monkeypatch.setattr("sys.argv", [
        "build_human_goldset.py", "--src", str(src),
        "--consent", str(tmp_path / "consent.csv"), "--out", str(out)])

    mod.main()
    summary = json.loads((out / "goldset-summary.json").read_text())
    assert summary["identities"] == 2
    assert summary["pairs"] == {"positive": 2, "negative": 2}
    assert summary["meets_recommendation"] is False  # 2/2 < 50/50 default
    assert summary["dry_scores"]["fully_separated"] is True
    blob = (out / "goldset-summary.json").read_text()
    assert "_ref_emb" not in blob and "_output_embs" not in blob
    assert (out / "frank" / "reference.png").is_file()
    assert (out / "gina" / "outputs" / "gina-2.jpg").is_file()
