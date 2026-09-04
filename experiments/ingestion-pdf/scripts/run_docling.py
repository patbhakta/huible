"""docling extraction (CPU) per sample, with timing."""
import json
import os
import pathlib
import time

os.environ.setdefault("HF_HOME", "/root/repos/huible/experiments/ingestion-pdf/.models/hf")

import torch

torch.set_num_threads(8)

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "outputs" / "docling"
OUT.mkdir(parents=True, exist_ok=True)

opts = PdfPipelineOptions()
opts.do_ocr = True
opts.do_table_structure = True
opts.table_structure_options.do_cell_matching = True
converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
)

samples = ["scanned_formula", "scanned_mixed", "chart_table", "real_mixed"]
results = {}
for name in samples:
    src = BASE / "samples" / f"{name}.pdf"
    t0 = time.perf_counter()
    conv = converter.convert(str(src))
    dt = time.perf_counter() - t0
    md = conv.document.export_to_markdown()
    (OUT / f"{name}.md").write_text(md)
    try:
        tables = conv.document.export_to_tables() if hasattr(conv.document, "export_to_tables") else None
    except Exception:
        tables = None
    results[name] = {
        "chars": len(md),
        "seconds": round(dt, 1),
        "status": str(conv.status),
        "n_tables": len(conv.document.tables) if conv.document.tables else 0,
    }
    print(name, results[name], flush=True)
print(json.dumps(results, indent=2))
