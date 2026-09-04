"""Measure the fastembed image-embedding lane (CPU/ONNX) for vault ingestion.

Questions answered:
1. Can the W1 local-ONNX lane family embed images at all, and with which models?
2. Cross-modal retrieval: can a text query pull the right image (CLIP text+vision
   pair, and jina-clip-v1 unified space)?
3. Cost: model disk footprint, load time, ms/image on CPU.

Images are the 150 DPI page rasters already produced by the HU-2692 PDF-extraction
pipeline (experiments/ingestion-pdf/outputs/page_png) — real repo-pipeline images.

Output: ../outputs/image_embed.json
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding
from fastembed.image.image_embedding import ImageEmbedding

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "outputs"
IMAGES_DIR = ROOT.parent / "ingestion-pdf" / "outputs" / "page_png"

# (query text, expected image substring) — retrieval must rank the right page first
QUERIES = [
    ("attention mechanism neural network architecture diagrams", "real_mixed"),
    ("scanned page of a physics paper with formulas", "scanned_formula"),
    ("scanned page with dense text and figures", "scanned_mixed"),
    ("bar chart of monthly service log revenue with a data table", "chart_table"),
]

MODELS = {
    "clip": {
        "vision": "Qdrant/clip-ViT-B-32-vision",
        "text": "Qdrant/clip-ViT-B-32-text",
    },
    "jina-clip-v1": {
        "vision": "jinaai/jina-clip-v1",
        "text": "jinaai/jina-clip-v1",
    },
}


def cache_dir_bytes(model_name: str) -> int:
    import os
    import tempfile

    leaf = model_name.replace("/", "--")
    roots = [
        os.environ.get("FASTEMBED_CACHE_PATH", ""),
        str(Path.home() / ".cache" / "fastembed"),
        os.path.join(tempfile.gettempdir(), "fastembed_cache"),
    ]
    for root in roots:
        d = Path(root) / f"models--{leaf}" if root else None
        if d and d.exists():
            seen: set[int] = set()
            total = 0
            for p in d.rglob("*"):
                if p.is_file() and p.stat().st_ino not in seen:
                    seen.add(p.stat().st_ino)
                    total += p.stat().st_size
            return total
    return 0


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> None:
    image_paths = sorted(IMAGES_DIR.glob("*.png"))
    if not image_paths:
        raise SystemExit(f"no images found in {IMAGES_DIR}")
    print(f"images: {[p.name for p in image_paths]}")

    report: dict[str, dict] = {}
    for name, pair in MODELS.items():
        entry: dict[str, dict] = {}

        t0 = time.perf_counter()
        vmodel = ImageEmbedding(pair["vision"])
        vision_load = time.perf_counter() - t0
        t0 = time.perf_counter()
        vecs = np.array(list(vmodel.embed([str(p) for p in image_paths])))
        embed_sec = time.perf_counter() - t0

        entry["vision"] = {
            "model": pair["vision"],
            "dim": vecs.shape[1],
            "load_sec": round(vision_load, 2),
            "ms_per_image": round(embed_sec * 1000 / len(image_paths), 1),
            "model_disk_bytes": cache_dir_bytes(pair["vision"]),
        }

        t0 = time.perf_counter()
        tmodel = TextEmbedding(pair["text"])
        text_load = time.perf_counter() - t0
        t0 = time.perf_counter()
        qvecs = np.array(list(tmodel.query_embed([q for q, _ in QUERIES])))
        text_embed_sec = time.perf_counter() - t0

        entry["text"] = {
            "model": pair["text"],
            "dim": qvecs.shape[1],
            "load_sec": round(text_load, 2),
            "ms_per_query": round(text_embed_sec * 1000 / len(QUERIES), 1),
            "model_disk_bytes": cache_dir_bytes(pair["text"]),
        }

        retrievals = []
        hits = 0
        for qi, (query, expected) in enumerate(QUERIES):
            sims = [cos(qvecs[qi], v) for v in vecs]
            ranked = sorted(zip(sims, [p.name for p in image_paths]), reverse=True)
            top = ranked[0][1]
            rank = next(i for i, (_, n) in enumerate(ranked, 1) if expected in n)
            hits += int(expected in top)
            retrievals.append(
                {
                    "query": query,
                    "expected": expected,
                    "top_image": top,
                    "expected_rank": rank,
                    "sims": {n: round(s, 4) for s, n in ranked},
                }
            )
            print(f"[{name}] '{query[:40]}...' -> top={top} (expected rank {rank})")

        entry["retrieval"] = {"top1_hits": hits, "n_queries": len(QUERIES), "detail": retrievals}

        # image-image self-similarity sanity (each image should match itself best)
        self_best = sum(
            1
            for i in range(len(image_paths))
            if max(range(len(image_paths)), key=lambda j: cos(vecs[i], vecs[j])) == i
        )
        entry["image_self_top1"] = f"{self_best}/{len(image_paths)}"
        report[name] = entry

    report["_images"] = [p.name for p in image_paths]
    (OUT / "image_embed.json").write_text(json.dumps(report, indent=2))
    print("wrote image_embed.json")


if __name__ == "__main__":
    main()
