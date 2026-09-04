"""Rasterize sample pages to PNG for VLM extraction."""
import pathlib

import pymupdf

BASE = pathlib.Path(__file__).resolve().parent.parent
PAGES = pathlib.Path(BASE / "outputs" / "page_png")
PAGES.mkdir(parents=True, exist_ok=True)

# single representative page per sample
targets = {
    "scanned_formula": 0,
    "scanned_mixed": 0,
    "chart_table": 0,
    "real_mixed": 3,
}
for name, idx in targets.items():
    doc = pymupdf.open(BASE / "samples" / f"{name}.pdf")
    page = doc[idx]
    pix = page.get_pixmap(dpi=150)
    out = PAGES / f"{name}_p{idx}.png"
    pix.save(out)
    print(out, out.stat().st_size, "bytes")
    doc.close()
