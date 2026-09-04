"""Build hard-sample PDF set for HU-2692 extraction experiments.

Samples:
  1. real_mixed.pdf      - real arXiv paper (formulas + tables + figures), native text
  2. scanned_formula.pdf - rasterized formula-heavy page, NO text layer (scanned-style)
  3. scanned_mixed.pdf   - rasterized mixed page, NO text layer
  4. chart_table.pdf     - synthetic chart + complex table page with known ground truth
Ground truth: ground_truth/*.txt (native text layer where available, authored text for synthetic).
"""
import io
import pathlib
import urllib.request

import fitz  # pymupdf

BASE = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = BASE / "samples"
GT = BASE / "ground_truth"
SAMPLES.mkdir(exist_ok=True)
GT.mkdir(exist_ok=True)

ARXIV_URL = "https://arxiv.org/pdf/1706.03762v7"
real_pdf = SAMPLES / "real_mixed.pdf"
if not real_pdf.exists():
    req = urllib.request.Request(ARXIV_URL, headers={"User-Agent": "Mozilla/5.0"})
    real_pdf.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    print("downloaded", real_pdf, real_pdf.stat().st_size, "bytes")

doc = fitz.open(real_pdf)
print("pages:", len(doc))

def rasterize_to_pdf(page: "fitz.Page", out: pathlib.Path, dpi: int = 200, jitter: bool = True):
    """Render a page to an image and wrap it in a text-free (scanned-style) PDF."""
    pix = page.get_pixmap(dpi=dpi)
    img = pix.tobytes("png")
    img_doc = fitz.open("pdf", fitz.open("png", img).convert_to_pdf())
    img_doc.save(out)
    img_doc.close()

# Page 4 (0-indexed 3) of Attention paper is formula/table heavy; page 5 has figures+table.
rasterize_to_pdf(doc[3], SAMPLES / "scanned_formula.pdf")
(SAMPLES / "scanned_formula.pdf.gt.txt").write_text(doc[3].get_text())
rasterize_to_pdf(doc[5], SAMPLES / "scanned_mixed.pdf")
(SAMPLES / "scanned_mixed.pdf.gt.txt").write_text(doc[5].get_text())
print("scanned samples built")

# --- synthetic chart/table page with authored ground truth -------------------
from PIL import Image, ImageDraw

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# chart image
fig, ax = plt.subplots(figsize=(6, 3.2))
months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
revenue = [12.4, 13.1, 15.8, 14.2, 17.9, 19.3]
ax.bar(months, revenue, color="#3b6ea5")
ax.set_title("Monthly Revenue H1 2026 (USD millions)")
ax.set_ylabel("USD millions")
ax.set_xlabel("Month")
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=150)
buf.seek(0)
chart_png = Image.open(buf).convert("RGB")

# scanned-style text image (ground truth known)
text_img = Image.new("RGB", (1700, 2200), "white")
d = ImageDraw.Draw(text_img)
try:
    from PIL import features

    font_ok = features.check("raqm") is not None
except Exception:
    font_ok = False
# default bitmap font keeps it dependency-free; slightly larger via scaling
TEXT_LINES = [
    "RIDGEFIELD AUTO REPAIR - SERVICE LOG",
    "Customer: Dana Whitfield    Vehicle: 2016 Honda Civic LX",
    "VIN: 2HGFC2F59GH554102    Odometer: 87,412 miles",
    "",
    "Complaint: Intermittent rattle from front-right suspension over",
    "potholes at speeds above 35 mph; noise disappears on smooth road.",
    "",
    "Diagnosis: Worn stabilizer bar end link bushing (right side).",
    "Sway bar bushings within spec; struts pass bounce test.",
    "",
    "Work performed: Replaced both stabilizer bar end links",
    " (OEM 51320-SVB-A02). Torqued to 40 Nm. Road tested 8 miles.",
    "",
    "Parts: 2x end link @ $38.50, 1x washer kit @ $6.20",
    "Labor: 1.2 hours @ $110/hr",
    "Total: $240.80    Warranty: 24 months parts and labor",
]
y = 60
for line in TEXT_LINES:
    d.text((70, y), line, fill="black")
    y += 42
text_img.save(SAMPLES / "_text_source.png")

# assemble chart+table page: text image on top, chart, then a drawn table
page_doc = fitz.open()
page = page_doc.new_page(width=612, height=1400)
rect = fitz.Rect(60, 40, 560, 380)
page.insert_image(rect, filename=str(SAMPLES / "_text_source.png"))
page.insert_image(fitz.Rect(60, 400, 552, 700), pixmap=fitz.Pixmap(buf.getvalue()))
# complex table with merged header via text
table_rows = [
    ["Quarter", "Region", "Units", "Unit price", "Revenue"],
    ["Q1 2026", "North", "1,204", "$212.00", "$255,248.00"],
    ["Q1 2026", "South", "987", "$205.00", "$202,335.00"],
    ["Q2 2026", "North", "1,510", "$214.50", "$323,895.00"],
    ["Q2 2026", "South", "1,044", "$209.90", "$219,135.60"],
]
tx = fitz.Rect(60, 730, 552, 730 + 30 * (len(table_rows) + 1))
page.insert_textbox(
    fitz.Rect(tx.x0, tx.y0 - 20, tx.x1, tx.y0),
    "Table 2: Regional unit sales, merged-quarter view",
    fontsize=10,
)
tbl = page.find_tables().table if False else None
# draw table manually: grid lines + cell text
col_w = (tx.x1 - tx.x0) / 5.0
row_h = 26.0
for i in range(len(table_rows) + 1):
    yy = tx.y0 + i * row_h
    page.draw_line(fitz.Point(tx.x0, yy), fitz.Point(tx.x1, yy), width=0.7)
for j in range(6):
    xx = tx.x0 + j * col_w
    page.draw_line(fitz.Point(xx, tx.y0), fitz.Point(xx, tx.y0 + len(table_rows) * row_h), width=0.7)
for r, row in enumerate(table_rows):
    for c, val in enumerate(row):
        page.insert_text(
            fitz.Point(tx.x0 + c * col_w + 4, tx.y0 + r * row_h + 17), val, fontsize=9
        )
page_doc.save(SAMPLES / "chart_table.pdf")
page_doc.close()

gt_table = (
    "Table 2: Regional unit sales, merged-quarter view\n"
    + "\n".join("\t".join(r) for r in table_rows)
)
GT.joinpath("chart_table.pdf.gt.txt").write_text(
    "\n".join(TEXT_LINES)
    + "\n\nMonthly Revenue H1 2026 (USD millions)\n"
    + "Jan Feb Mar Apr May Jun\n12.4 13.1 15.8 14.2 17.9 19.3\n\n"
    + gt_table
)
print("synthetic samples built")
