"""Caveat test: does Flash extract anything from scanned (image-only) PDFs?

Founder reframe item 5: Flash indexes TEXT-based PDFs; scanned pages may
need our extraction (Docling/VLM) as a front end. This runs Flash's
LLM-free path (summary=False, optimize=merge) on all four torture-page
PDFs and records per-PDF: text chars extracted, structure found, error.

Outcome decides which comparison shape the eval takes:
  A) [PageIndex Flash native] vs [Docling -> our retrieval]  (text PDFs)
  B) [Docling extraction -> PageIndex md_to_tree] vs
     [Docling extraction -> our retrieval]                   (scans: PageIndex
                                                              = candidate
                                                              component)
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EVAL_DIR

SAMPLES = Path("/root/repos/huible/experiments/ingestion-pdf/samples")
OUT = EVAL_DIR / "outputs" / "flash-scan-caveat.json"


def main() -> None:
    import pypdfium2 as pdfium
    from pageindex.flash import page_index_flash

    results = {}
    for pdf in sorted(SAMPLES.glob("*.pdf")):
        doc = pdfium.PdfDocument(str(pdf))
        chars = sum(
            len(doc[i].get_textpage().get_text_range())
            for i in range(len(doc))
        )
        entry = {"pages": len(doc), "text_chars": chars}
        if chars < 100:
            entry["verdict"] = "no text layer -> flash LLM-free path skipped"
        else:
            t0 = time.time()
            try:
                res = page_index_flash(
                    str(pdf), summary=False, optimize="merge")
                entry.update(
                    structure_nodes=_count_nodes(res.get("structure", [])),
                    toc_source=res.get("toc_source"),
                    wall_s=round(time.time() - t0, 1),
                )
                entry["verdict"] = "structure extracted"
            except Exception as e:  # noqa: BLE001 - record everything
                entry["verdict"] = f"FAILED: {type(e).__name__}: {e}"[:300]
        results[pdf.name] = entry
        print(pdf.name, json.dumps(entry)[:240])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2))
    print(f"\nsaved -> {OUT}")


def _count_nodes(nodes):
    n = 0
    stack = list(nodes)
    while stack:
        cur = stack.pop()
        n += 1
        stack.extend(cur.get("nodes", []) or [])
    return n


if __name__ == "__main__":
    main()
