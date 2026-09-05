"""Shape-B smoke: Docling extraction -> PageIndex tree (markdown lane).

The scan-caveat test showed Flash cannot index image-only pages (0 text
chars on the scanned torture PDFs), so for scans PageIndex competes as a
candidate COMPONENT: [Docling extraction -> PageIndex md_to_tree] vs
[Docling extraction -> our retrieval].

This runs md_to_tree on the existing Docling .md extractions of the four
torture-page PDFs, summaries OFF (LLM-free — structure topology only) and
records node counts + wall time.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EVAL_DIR

DOCLING = Path("/root/repos/huible/experiments/ingestion-pdf/outputs/docling")
OUT = EVAL_DIR / "outputs" / "docling-composite-smoke.json"


def count_nodes(nodes):
    n = 0
    stack = list(nodes)
    while stack:
        cur = stack.pop()
        n += 1
        stack.extend(cur.get("nodes", []) or [])
    return n


def main() -> None:
    from pageindex.page_index_md import md_to_tree

    results = {}
    for md in sorted(DOCLING.glob("*.md")):
        t0 = time.time()
        try:
            tree = asyncio.run(md_to_tree(
                md_path=str(md),
                if_thinning=False,
                if_add_node_summary="no",
                if_add_doc_description="no",
                if_add_node_text="no",
                if_add_node_id="yes",
            ))
            structure = tree.get("structure", tree if isinstance(tree, list) else [])
            results[md.name] = {
                "nodes": count_nodes(structure),
                "doc_title": (tree.get("doc_title") if isinstance(tree, dict) else None),
                "wall_s": round(time.time() - t0, 2),
                "llm_free": True,
            }
        except Exception as e:  # noqa: BLE001 - record everything
            results[md.name] = {
                "error": f"{type(e).__name__}: {e}"[:300],
                "wall_s": round(time.time() - t0, 2),
            }
        print(md.name, json.dumps(results[md.name])[:200])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
