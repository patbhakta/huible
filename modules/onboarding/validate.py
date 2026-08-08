#!/usr/bin/env python3
"""
Huible Onboarding — Stage 4: VALIDATE

Validates OKF v0.2 conformance for a generated persona vault directory.

Checks per vault:
  - Required files present: persona-profile.md, sample-dialog.md
  - YAML frontmatter present, delimited by ---
  - Required frontmatter keys: type, title, status, generated (by + at), tags
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
        "min_list_items": 3,
    },
    "sample-dialog.md": {
        "h1_contains": "Sample Dialog",
        "required_sections": [
            "Key Topics",
            "Notable Quotes",
        ],
        "min_list_items": 3,
    },
}

REQUIRED_FRONTMATTER_TOP = ["type", "title", "status", "tags"]
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


def add_check(checks, name, key, status, detail=""):
    checks.append({"check": f"{name}:{key}", "status": status, "detail": detail})


def validate_file(name, spec, filepath, checks):
    """Validate a single OKF document. Returns list of check dicts."""
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

    # Required top-level frontmatter keys.
    for key in REQUIRED_FRONTMATTER_TOP:
        if key in fm:
            add_check(checks, name, f"fm_{key}", "pass")
        else:
            add_check(checks, name, f"fm_{key}", "fail", f"missing frontmatter key: {key}")

    # generated block (by + at).
    for sub in ("by", "at"):
        full = f"generated.{sub}"
        if full in fm:
            add_check(checks, name, f"fm_generated_{sub}", "pass")
        else:
            add_check(checks, name, f"fm_generated_{sub}", "fail", f"missing generated.{sub}")

    # generated.at should look like an ISO-8601 timestamp.
    at = fm.get("generated.at")
    if at:
        iso_ok = False
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                datetime.strptime(strip_quotes(at), fmt)
                iso_ok = True
                break
            except ValueError:
                pass
        if iso_ok:
            add_check(checks, name, "fm_generated_at_iso", "pass")
        else:
            add_check(
                checks, name, "fm_generated_at_iso", "warn", f"generated.at not ISO-8601 Z: {at}"
            )
    else:
        add_check(checks, name, "fm_generated_at_iso", "skip", "generated.at absent")

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
        description="Validate OKF v0.2 conformance for a persona vault"
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
