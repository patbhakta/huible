#!/usr/bin/env python3
"""
Huible Onboarding — Stage 5: VALIDATE

Validates persona vault documents against the flat, Librarian-governed OKF
frontmatter standard (the two-field model: ``tags`` + ``updated``). The older
OKF v0.2 spec (``type``/``title``/``status``/``generated`` and its strict
validator) was retired vault-wide; this validator was reconciled to the new
standard (the generator in ``structure.py`` emits the same two-field model and
carries provenance in the document body).

Checks per vault:
  - Required files present: persona-profile.md, sample-dialog.md
  - YAML frontmatter present, delimited by ---
  - Required frontmatter keys: tags, updated
  - `updated` looks like an ISO-8601 date (YYYY-MM-DD or full timestamp)
  - Required body sections per document type
  - Non-trivial content (no empty stubs)

Usage:
  python3 validate.py --dir <vault-dir> --output <report.json>
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

REQUIRED_FILES = {
    "persona-profile.md": {
        "h1_contains": "Persona Profile",
        "required_sections": [
            "Communication Style",
            "Humor Type",
            "Core Traits",
        ],
        # Optional sections: validated for non-trivial content ONLY when present
        # (e.g. multimodal onboarding adds Vocal Patterns & Prosody, BHAA-1375).
        "optional_sections": ["Vocal Patterns & Prosody"],
        "min_list_items": 3,
    },
    "sample-dialog.md": {
        "h1_contains": "Sample Dialog",
        "required_sections": [
            "Key Topics",
            "Notable Quotes",
        ],
        "optional_sections": [],
        "min_list_items": 3,
    },
}

# Flat Librarian-governed OKF standard: only these two frontmatter keys are
# required. The retired OKF v0.2 keys (type/title/status/generated) are no
# longer enforced.
REQUIRED_FRONTMATTER_TOP = ["tags", "updated"]
FRONTMATTER_DELIM = "---"


def parse_frontmatter(text):
    """Return (frontmatter_text, body_text) or (None, text) if absent."""
    stripped = text.lstrip("\n")
    if not stripped.startswith(FRONTMATTER_DELIM):
        return None, text
    lines = stripped.splitlines()
    # First line is the opening delimiter.
    body_start = 1
    fm_lines = []
    closed = False
    for line in lines[1:]:
        body_start += 1
        if line.strip() == FRONTMATTER_DELIM:
            closed = True
            break
        fm_lines.append(line)
    if not closed:
        return None, text
    fm_text = "\n".join(fm_lines)
    body_text = "\n".join(lines[body_start:])
    return fm_text, body_text


def parse_simple_yaml(fm_text):
    """Parse the flat/one-level YAML these scripts generate into a dict.

    Handles scalar keys and inline list values [a, b, c]. Nested blocks
    (generated:) are collapsed into dotted keys (generated.by, generated.at).
    """
    data = {}
    current_prefix = ""
    for raw in fm_text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            current_prefix = ""
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if indent > 0:
            prefix = current_prefix
        else:
            prefix = ""
            current_prefix = key
        full_key = f"{prefix}.{key}" if prefix else key
        if value == "":
            # Nested block follows; track it as the current prefix.
            current_prefix = full_key
            continue
        data[full_key] = value
    return data


def strip_quotes(value):
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def _section_body(body: str, section: str) -> str:
    """Return the text under a `## <section>` header up to the next `##` header."""
    lines = body.splitlines()
    out: list[str] = []
    inside = False
    target = section.lower()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if inside:
                break  # reached the next section
            inside = stripped[3:].strip().lower() == target
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def add_check(checks, name, key, status, detail=""):
    checks.append({"check": f"{name}:{key}", "status": status, "detail": detail})


def validate_file(name, spec, filepath, checks):
    """Validate a single persona document. Returns list of check dicts."""
    if not os.path.exists(filepath):
        add_check(checks, name, "file_present", "fail", f"missing file: {name}")
        return checks
    add_check(checks, name, "file_present", "pass")

    with open(filepath, encoding="utf-8", errors="replace") as f:
        text = f.read()

    fm_text, body = parse_frontmatter(text)
    if fm_text is None:
        add_check(
            checks, name, "frontmatter_present", "fail", "no YAML frontmatter delimited by ---"
        )
        return checks
    add_check(checks, name, "frontmatter_present", "pass")

    fm = parse_simple_yaml(fm_text)

    # Required top-level frontmatter keys (flat Librarian-governed OKF model).
    for key in REQUIRED_FRONTMATTER_TOP:
        if key in fm:
            add_check(checks, name, f"fm_{key}", "pass")
        else:
            add_check(checks, name, f"fm_{key}", "fail", f"missing frontmatter key: {key}")

    # `updated` should look like an ISO-8601 date (YYYY-MM-DD) or a full
    # timestamp. Accept both so a hand-edited date or a generated timestamp pass.
    updated = fm.get("updated")
    if updated:
        updated_ok = False
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                datetime.strptime(strip_quotes(updated), fmt)
                updated_ok = True
                break
            except ValueError:
                pass
        if updated_ok:
            add_check(checks, name, "fm_updated_iso", "pass")
        else:
            add_check(
                checks, name, "fm_updated_iso", "warn", f"updated not ISO-8601 date: {updated}"
            )
    else:
        add_check(checks, name, "fm_updated_iso", "skip", "updated absent")

    # Body: H1 present.
    h1s = re.findall(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    h1_ok = any(spec["h1_contains"].lower() in h.lower() for h in h1s)
    if h1_ok:
        add_check(checks, name, "h1_present", "pass")
    else:
        add_check(checks, name, "h1_present", "fail", f"no H1 containing '{spec['h1_contains']}'")

    # Body: required sections (## headers).
    headers = re.findall(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    header_lowers = [h.lower() for h in headers]
    for section in spec["required_sections"]:
        key = f"section_{section.lower().replace(' ', '_')}"
        if section.lower() in header_lowers:
            add_check(checks, name, key, "pass")
        else:
            add_check(checks, name, key, "fail", f"missing section: {section}")

    # Optional sections (multimodal etc.): soft-check content only when present.
    for section in spec.get("optional_sections", []):
        if section.lower() in header_lowers:
            key = f"section_{section.lower().replace(' ', '_')}"
            sub = _section_body(body, section)
            sub_items = len(re.findall(r"^\s*-\s+\S", sub, flags=re.MULTILINE)) + len(
                re.findall(r"^\s*\*\*[^:]+:\*\*", sub, flags=re.MULTILINE)
            )
            if sub_items >= 1:
                add_check(checks, name, key, "pass", f"{sub_items} item(s)")
            else:
                add_check(checks, name, key, "warn", f"empty optional section: {section}")

    # Body: non-trivial content (list items or blockquotes).
    list_items = len(re.findall(r"^\s*-\s+\S", body, flags=re.MULTILINE))
    quotes = len(re.findall(r"^\s*>\s+\S", body, flags=re.MULTILINE))
    content_count = list_items + quotes
    minimum = spec["min_list_items"]
    if content_count >= minimum:
        add_check(checks, name, "non_trivial_content", "pass", f"{content_count} list/quote items")
    else:
        add_check(
            checks,
            name,
            "non_trivial_content",
            "fail",
            f"only {content_count} list/quote items (min {minimum})",
        )

    return checks


def main():
    parser = argparse.ArgumentParser(
        description="Validate persona vault frontmatter + body conformance (flat OKF standard)"
    )
    parser.add_argument("--dir", required=True, help="Persona vault directory to validate")
    parser.add_argument("--output", help="Optional JSON report output path")
    args = parser.parse_args()

    vault_dir = args.dir
    if not os.path.isdir(vault_dir):
        print(f"ERROR: vault directory not found: {vault_dir}", file=sys.stderr)
        sys.exit(2)

    checks = []
    for name, spec in REQUIRED_FILES.items():
        validate_file(name, spec, os.path.join(vault_dir, name), checks)

    passed = sum(1 for c in checks if c["status"] == "pass")
    failed = sum(1 for c in checks if c["status"] == "fail")
    warned = sum(1 for c in checks if c["status"] == "warn")
    overall = "pass" if failed == 0 else "fail"

    report = {
        "vault_dir": os.path.abspath(vault_dir),
        "overall": overall,
        "summary": {
            "total_checks": len(checks),
            "passed": passed,
            "failed": failed,
            "warned": warned,
        },
        "checks": checks,
    }

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)

    print(f"Vault: {vault_dir}")
    print(f"Overall: {overall} ({passed} passed, {failed} failed, {warned} warned)")
    for c in checks:
        if c["status"] != "pass":
            print(f"  [{c['status'].upper()}] {c['check']}: {c['detail']}")

    print(f"::{json.dumps({'outputs': report})}::")

    sys.exit(0 if overall == "pass" else 1)


if __name__ == "__main__":
    main()
