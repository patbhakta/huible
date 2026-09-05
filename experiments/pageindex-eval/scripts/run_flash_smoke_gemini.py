"""HU-2726: PageIndex tree-gen rerun on the Gemini flash ladder.

Same 22-page q1-fy25 earnings PDF, same UsageLedger methodology as
run_flash_smoke.py (the z.ai glm-5.3-flash arm), but every LLM call goes
to gemini-3.8-flash via litellm's native gemini provider (GEMINI_API_KEY
from the repo .env). Env-switch by design — no SDK changes.

Output: outputs/smoke-gemini-q1fy25/ (kept separate from the z.ai arm's
outputs/smoke-flash-q1fy25/ so both trees stay comparable).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import (EVAL_DIR, UsageLedger, apply_zai_throttle, new_client,
                    set_gemini_lane)

PDF = Path("/root/repos/pageindex/examples/documents/q1-fy25-earnings.pdf")
OUT = EVAL_DIR / "outputs" / "smoke-gemini-q1fy25"


def main() -> None:
    model = set_gemini_lane()
    ledger = UsageLedger(model=model)
    # Same serialization the z.ai arm used: 1 in flight + 429 backoff.
    # Gemini flash free/paid tiers both throttle; the wrapper is generic.
    apply_zai_throttle(max_concurrency=1, ledger=ledger)
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(PDF))
    n_pages = len(pdf)
    n_chars = sum(
        len(pdf[i].get_textpage().get_text_range()) for i in range(n_pages)
    )
    print(f"corpus: {PDF.name} pages={n_pages} text_chars={n_chars}")
    print(f"lane: {model}")

    storage = OUT / ".pageindex"
    client = new_client(storage, index_model=model)

    t0 = time.time()
    doc = client.submit_document(str(PDF), wait=True)
    index_s = time.time() - t0
    doc_id = doc["doc_id"]
    print(f"indexed doc_id={doc_id} wall={index_s:.1f}s")

    tree = client.get_tree(doc_id, node_summary=True)["result"]

    def walk(nodes, depth=0):
        for n in nodes:
            title = n.get("title", "?")
            span = f"p{n['start_index']}-{n.get('end_index')}" if n.get(
                "start_index") is not None else "p?"
            summ = (n.get("summary") or "")[:100].replace("\n", " ")
            print("  " * depth + f"- {title} [{span}] {summ}")
            walk(n.get("nodes", []) or [], depth + 1)

    print("\n=== TREE ===")
    walk(tree)

    usage = ledger.save(OUT / "usage_index.json")
    usage.update(pdf_name=PDF.name, pages=n_pages, text_chars=n_chars,
                 index_wall_s=round(index_s, 1),
                 s_per_page=round(index_s / n_pages, 2),
                 tokens_per_page=round(
                     usage["total_tokens"] / n_pages, 1))
    (OUT / "index_meta.json").write_text(__import__("json").dumps(usage, indent=2))
    print("\n=== USAGE (index) ===")
    for k, v in usage.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
