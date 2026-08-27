"""Shared helpers for the reference-grounded identity pipeline (HU-2150).

Stages: ref_collect → ref_curate → generate_image --ref-image → ref_gate →
ref_registry. See docs/IDENTITY_IMAGE_PIPELINE.md.

Requires: insightface, onnxruntime, opencv-python-headless, pillow, imagehash
(scripts/requirements-refpipe.txt). Model weights (buffalo_l) cache to
~/.insightface/models/ on first use; CPU-only is sufficient.
"""

import datetime
import hashlib
import json
import os

import cv2
import numpy as np

# Module-level singleton — loading buffalo_l takes ~5 s; do it once per process.
_APP = None


def face_app():
    global _APP
    if _APP is None:
        from insightface.app import FaceAnalysis

        _APP = FaceAnalysis(name="buffalo_l")
        _APP.prepare(ctx_id=-1, det_size=(640, 640))
    return _APP


def now_iso():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def append_jsonl(path, record):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=False) + "\n")


def primary_face(img_bgr):
    """Largest detected face or None. Returns (face, metrics_dict)."""
    faces = face_app().get(img_bgr)
    if not faces:
        return None, {"faces": 0}
    f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
    h, w = img_bgr.shape[:2]
    fw = float(f.bbox[2] - f.bbox[0])
    area_frac = (fw * float(f.bbox[3] - f.bbox[1])) / (w * h)
    yaw, pitch, roll = [float(v) for v in getattr(f, "pose", (0.0, 0.0, 0.0))]
    m = {
        "faces": len(faces),
        "face_w_px": int(fw),
        "face_area_frac": round(area_frac, 4),
        "yaw": round(yaw, 1),
        "pitch": round(pitch, 1),
        "roll": round(roll, 1),
    }
    return f, m


def face_embedding(path):
    """(normed 512-d embedding, metrics) for the primary face, or (None, metrics)."""
    img = cv2.imread(path)
    if img is None:
        return None, {"faces": 0, "error": "unreadable_image"}
    f, m = primary_face(img)
    if f is None:
        return None, m
    return np.asarray(f.normed_embedding, dtype=np.float32), m


def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def valid_rights(rec):
    """Fail-closed rights check per the COLLECT schema (docs §1)."""
    r = rec.get("rights") or {}
    basis = r.get("basis")
    if basis == "client_upload":
        return bool(r.get("consent_by"))
    if basis == "license":
        return bool(r.get("license_ref"))
    if basis == "synthetic":
        return rec.get("source") == "synthetic_seed"
    return False


def vault_paths(persona_root):
    return {
        "raw_dir": os.path.join(persona_root, "references", "raw"),
        "set_json": os.path.join(persona_root, "references", "reference-set.json"),
        "curated": os.path.join(persona_root, "references", "curated.jsonl"),
        "cur_log": os.path.join(persona_root, "references", "curation-log.jsonl"),
        "emb": os.path.join(persona_root, "references", "embeddings.json"),
        "gate_config": os.path.join(persona_root, "references", "gate-config.json"),
        "gate_log": os.path.join(persona_root, "media", "identity-gate-log.jsonl"),
        "registry": os.path.join(persona_root, "media", "identity-registry.jsonl"),
    }
