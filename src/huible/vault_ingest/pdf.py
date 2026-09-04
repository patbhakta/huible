"""PDF ingestion: per-page router (Tier 0 direct / Tier 1 docling CPU).

Router (measured basis, HU-2692): pymupdf text-layer check per page —
pages with a real text layer extract directly via pymupdf (Tier 0,
near-instant, F1 1.0 on native pages); pages with no/short text layer are
rasterized at 200 DPI and run through docling CPU (Tier 1, MIT license,
F1 0.93-0.98 on the scanned samples).

Tier-2 (VLM) detection runs on Tier-1 output: ``formula-not-decoded`` markers
mark formula regions. The VLM pass itself is flag-gated OFF by default
(see ``vlm.py``); with the flag off the page is retained as a source-of-truth
image artifact in the vault and flagged ``vlm_pending``/``vlm_skipped``.

Tier mapping end-to-end:
- verbatim page text + page/offset provenance -> vault
- OCR'd text + table structure (markdown) -> vault
- formula LaTeX (VLM lane, when enabled) -> vault
- rasterized page intermediates -> TencentDB tier, EXCEPT pages with
  low-confidence regions (formula markers) where the image is retained in the
  vault as source-of-truth artifact
- approximate chart values (VLM lane, when enabled) -> TencentDB tier
"""

from __future__ import annotations

import time
from pathlib import Path

from .atoms import Tier, VaultWriter, atom_from
from .config import IngestConfig
from .vlm import DISABLED_REASON, UNCONFIGURED_REASON, VLMDisabledError, vlm_page_pass

FORMULA_MARKER = "formula-not-decoded"

LAZY_DOCLING_HINT = (
    "docling is required for the Tier-1 lane; install the ingest extras "
    "(pip install 'huible[ingest]')"
)


def _lazy_pymupdf():
    try:
        import pymupdf
    except ImportError as e:  # pragma: no cover - environment guard
        raise RuntimeError(f"pymupdf is required for PDF routing: {e}") from e
    return pymupdf


def _lazy_docling():
    try:
        import torch

        torch.set_num_threads(8)
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as e:  # pragma: no cover - environment guard
        raise RuntimeError(f"{LAZY_DOCLING_HINT}: {e}") from e

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = True
    opts.table_structure_options.do_cell_matching = True
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: PdfFormatOption(pipeline_options=opts),
        }
    )


def route_page(page) -> str:
    """Per-page router decision: ``tier0`` (direct text) or ``tier1`` (raster + docling)."""
    text = page.get_text("text").strip()
    return "tier0" if len(text) >= IngestConfig.text_layer_min_chars else "tier1"


def _rasterize_page(doc, page_index: int, out_png: Path, dpi: int) -> Path:
    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi)
    pix.save(out_png)
    return out_png


def ingest_pdf(
    path: str | Path,
    writer: VaultWriter,
    config: IngestConfig | None = None,
    converter=None,
) -> dict:
    """Ingest one PDF through the per-page router. Returns a run report."""
    config = config or IngestConfig()
    pymupdf = _lazy_pymupdf()
    src = Path(path)
    t0 = time.perf_counter()
    original = writer.store_original(src)

    doc = pymupdf.open(src)
    pages_report: list[dict] = []
    tier0_texts: list[str] = []
    docling_pages = 0

    try:
        for i, page in enumerate(doc):
            decision = route_page(page)
            entry: dict = {"page": i, "route": decision}

            if decision == "tier0":
                text = page.get_text("text")
                tier0_texts.append(text)
                writer.write_atom(
                    atom_from(
                        "doc_page_text",
                        Tier.VAULT,
                        {
                            "file": original["stored_as"],
                            "sha256": original["sha256"],
                            "page": i,
                        },
                        {
                            "tool": f"pymupdf {pymupdf.__version__} (text layer, tier 0)",
                            "extraction": "native text layer",
                        },
                        {"text": text},
                    ),
                    slug=f"{src.stem}_p{i}",
                )
                if page.get_images(full=True):
                    entry["flags"] = ["has_images_vlm_pending"]
            else:
                docling_pages += 1
                entry.update(
                    _ingest_page_tier1(doc, i, src.stem, writer, config, converter, original)
                )

            pages_report.append(entry)
    finally:
        doc.close()

    return {
        "input": str(src),
        "sha256": original["sha256"],
        "pages": pages_report,
        "tier0_pages": sum(1 for p in pages_report if p["route"] == "tier0"),
        "tier1_pages": docling_pages,
        "wall_sec": round(time.perf_counter() - t0, 2),
    }


def _ingest_page_tier1(
    doc,
    page_index: int,
    stem: str,
    writer: VaultWriter,
    config: IngestConfig,
    converter,
    original: dict,
) -> dict:
    """Raster + docling CPU lane for one page, with tier-2 detection."""
    if converter is None:
        converter = _lazy_docling()
    dpi = config.raster_dpi
    raster = writer.derived_media_dir / f"{stem}_p{page_index}_dpi{dpi}.png"
    _rasterize_page(doc, page_index, raster, dpi)

    conv = converter.convert(str(raster))
    markdown = conv.document.export_to_markdown()

    formula_count = markdown.count(FORMULA_MARKER)
    flags: list[str] = []
    if formula_count:
        flags.append(f"{FORMULA_MARKER}x{formula_count}")
        # Low-confidence region: retain the page image as source-of-truth artifact.
        vault_png = writer.vault_dir / "page_png" / raster.name
        vault_png.parent.mkdir(parents=True, exist_ok=True)
        raster.replace(vault_png)
        flags.append("page_image_retained_vault")
        raster_rel = str(vault_png.relative_to(writer.root))
        raster_final = vault_png
    else:
        # No low-confidence regions: raster stays a regenerable intermediate.
        flags.append("raster_intermediate_derived")
        raster_rel = str(raster.relative_to(writer.root))
        raster_final = raster

    writer.write_atom(
        atom_from(
            "doc_page_ocr",
            Tier.VAULT,
            {"file": original["stored_as"], "sha256": original["sha256"], "page": page_index},
            {
                "tool": f"docling (cpu, {dpi} dpi raster)",
                "extraction": "ocr + table structure",
                "page_image": raster_rel,
                "flags": flags,
            },
            {"markdown": markdown, "tables": _tables_markdown(conv)},
        ),
        slug=f"{stem}_p{page_index}",
    )

    entry = _vlm_stage(raster_final, config, writer, page_index)
    entry["flags"] = flags
    return entry


def _vlm_stage(page_png: Path, config: IngestConfig, writer: VaultWriter, page_index: int) -> dict:
    """Tier-2 VLM hook for a page image. Flag-gated OFF by default."""
    try:
        result = vlm_page_pass(str(page_png), config)
    except VLMDisabledError as e:
        reason = DISABLED_REASON if str(e) == DISABLED_REASON else UNCONFIGURED_REASON
        return {"vlm": {"status": "skipped", "reason": reason, "page_png": str(page_png)}}

    writer.write_atom(
        atom_from(
            "doc_page_vlm",
            Tier.VAULT,
            {"origin_pdf_page": page_index},
            {"tool": "vlm page pass", "page_png": str(page_png)},
            {"formulas_latex": result.get("formulas_latex", [])},
        )
    )
    if result.get("chart_values"):
        writer.write_atom(
            atom_from(
                "chart_values",
                Tier.DERIVED,
                {"origin_pdf_page": page_index},
                {"tool": "vlm page pass", "accuracy": "approximate"},
                {"values": result["chart_values"], "flags": ["approximate", "chart-derived"]},
            )
        )
    return {"vlm": {"status": "ok", "page_png": str(page_png)}}


def _tables_markdown(conv) -> list[str]:
    try:
        return [conv.document.export_to_markdown(t) for t in (conv.document.tables or [])]
    except Exception:
        return []
