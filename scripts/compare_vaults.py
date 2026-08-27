#!/usr/bin/env python3
"""
Kestra worker — Vault comparison harness (Option 2).

Runs the same test question through every persona vault via generate_answer.py,
then assembles a side-by-side comparison report:
  - each vault's OKF validation verdict (existing Librarian standard validator)
  - each vault's answer, verbatim
  - rough metrics: answer length, overlap of distinctive words between vaults

Usage:
  python3 compare_vaults.py --vaults-root /root/repos/personas/chandler-bing \
      --persona "Chandler Bing" --question "..." \
      --out /root/repos/personas/chandler-bing/comparison/2026-08-27.md \
      [--answers-dir /tmp/vault-compare] [--model glm-5.3] [--no-llm]

--no-llm skips generation and validates + compares vault structure only.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
GENERATE = os.path.join(HERE, "generate_answer.py")


def validate_vault(vault_dir):
    """Returns dict from the OKF validator (Librarian two-field standard).
    The validator exits non-zero when a vault fails checks — that's a valid
    report, not an error, so parse the report regardless of exit code."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out = tf.name
    try:
        subprocess.run(
            [sys.executable, "/root/repos/huible/modules/onboarding/validate.py",
             "--dir", vault_dir, "--output", out],
            capture_output=True, text=True, timeout=60,
        )
        with open(out) as f:
            report = json.load(f)
        checks = report.get("checks", [])
        failed = [c for c in checks if c.get("status") == "fail"]
        return {
            "ok": report.get("overall") == "pass",
            "overall": report.get("overall"),
            "failed": [f"{c.get('check')}: {c.get('detail')}" for c in failed],
            "passed": report.get("summary", {}).get("passed", 0),
            "total": report.get("summary", {}).get("total_checks", 0),
        }
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "overall": "error", "failed": [str(e)[:200]],
                "passed": 0, "total": 0}
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def generate_answer(vault_dir, persona, question, model, out_file, engine="glm"):
    cmd = [sys.executable, GENERATE,
           "--vault-dir", vault_dir,
           "--question", question,
           "--persona", persona,
           "--engine", engine,
           "--out-file", out_file]
    if model:
        cmd += ["--model", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "unknown error").strip()[:400]
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1]), None
    except (json.JSONDecodeError, IndexError):
        return None, f"unparseable generator output: {proc.stdout[:300]}"


STOPWORDS = set("""a an the and or but if then else when while of to in on at for with about
as by from up down out over under again further once here there all any both each few more
most other some such no nor not only own same so than too very can will just don should now
i me my we our you your he him his she her it its they them their what which who whom this
that these those am is are was were be been being do does did doing have has had having would
could might must shall may""".split())


def distinctive_words(text, top=12):
    counts = {}
    for w in text.lower().replace(",", " ").replace(".", " ").split():
        w = w.strip("\"'!?;:()")
        if w and w not in STOPWORDS and len(w) > 3:
            counts[w] = counts.get(w, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:top]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vaults-root", required=True)
    ap.add_argument("--persona", default="Chandler Bing")
    ap.add_argument("--question", default="How would you comfort a friend who just lost their job?")
    ap.add_argument("--out", required=True)
    ap.add_argument("--answers-dir", default="/tmp/vault-compare")
    ap.add_argument("--engine", choices=("glm", "gemini"), default="glm")
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    vault_ids = ["01-garbage", "02-clean", "03-transcripts", "04-multimodal"]
    vaults = []
    for vid in vault_ids:
        d = os.path.join(args.vaults_root, vid)
        if os.path.isdir(d):
            vaults.append((vid, d))

    os.makedirs(args.answers_dir, exist_ok=True)
    results = []
    for vid, d in vaults:
        entry = {"id": vid, "dir": d}
        entry["okf"] = validate_vault(d)
        if not args.no_llm:
            ans_file = os.path.join(args.answers_dir, f"{vid}.md")
            answer, err = generate_answer(d, args.persona, args.question, args.model, ans_file, engine=args.engine)
            if answer:
                entry["answer"] = answer["answer"]
                entry["answer_file"] = ans_file
            else:
                entry["answer_error"] = err
        results.append(entry)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    lines = [
        "---",
        f"tags: [huible, persona, comparison]",
        f"updated: {datetime.now().strftime('%Y-%m-%d')}",
        "---",
        "",
        f"# Vault Comparison — {args.persona}",
        "",
        f"**Question:** {args.question}  ",
        f"**Engine:** {args.engine} ({args.model or ('gemini-3.7-flash' if args.engine == 'gemini' else 'glm-5.3')})  ",
        f"**Generated:** {datetime.now().isoformat(timespec='seconds')} via Kestra `huible-vault-compare`",
        "",
        "| Vault | OKF | Checks | Answer length (words) |",
        "|---|---|---|---|",
    ]
    for r in results:
        okf = r["okf"]
        okf_cell = "✅ pass" if okf.get("ok") else "❌ " + "; ".join(okf.get("failed", []))[:80]
        checks_cell = f"{okf.get('passed', 0)}/{okf.get('total', 0)}"
        length = len(r.get("answer", "").split()) if r.get("answer") else "—"
        lines.append(f"| {r['id']} | {okf_cell} | {checks_cell} | {length} |")
    lines.append("")
    for r in results:
        lines += [f"## {r['id']}", ""]
        if r.get("answer"):
            lines += [f"> {r['answer']}", ""]
            words = distinctive_words(r["answer"])
            if words:
                lines += ["**Distinctive words:** " + ", ".join(f"{w} ({c})" for w, c in words), ""]
        elif r.get("answer_error"):
            lines += [f"⚠️ generation failed: {r['answer_error']}", ""]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(json.dumps({
        "ok": all(r["okf"].get("ok") for r in results),
        "vaults": len(results),
        "okf_pass": sum(1 for r in results if r["okf"].get("ok")),
        "answers": sum(1 for r in results if r.get("answer")),
        "report": args.out,
    }))


if __name__ == "__main__":
    main()
