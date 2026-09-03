"""B3 abuse-log exclusion (HU-2447 V2 Curate) — unit tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vault_abuse_exclusion.py"


def _run(tmp_path, records, patterns_file=None):
    inp = tmp_path / "in.jsonl"
    inp.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    out = tmp_path / "curated.jsonl"
    audit = tmp_path / "audit.jsonl"
    report = tmp_path / "report.json"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--input",
        str(inp),
        "--output",
        str(out),
        "--audit",
        str(audit),
        "--report",
        str(report),
    ]
    if patterns_file:
        cmd += ["--patterns-file", str(patterns_file)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    kept = [json.loads(line) for line in out.read_text().splitlines() if line]
    audited = [json.loads(line) for line in audit.read_text().splitlines() if line]
    return kept, audited, json.loads(report.read_text())


def _rec(text: str, speaker: str = "PERSONA") -> dict:
    return {"speaker": speaker, "text": text}


def test_clean_banter_passes_through(tmp_path):
    """Sitcom banter/sarcasm is voice, not abuse — must never be excluded."""
    records = [
        _rec("Could I BE any more sorry?"),
        _rec("Shut up, you did not!"),
        _rec("Oh. My. God."),
        _rec("I say it so you don't have to."),
    ]
    kept, audited, report = _run(tmp_path, records)
    assert len(kept) == 4
    assert audited == []
    assert report["excluded_records"] == 0
    assert report["kept_records"] == 4


def test_dehumanization_excluded(tmp_path):
    records = [
        _rec("You are worthless and everyone knows it."),
        _rec("You're such a pathetic excuse for a friend."),
        _rec("Honestly, best day of my life."),
    ]
    kept, _audited, report = _run(tmp_path, records)
    assert len(kept) == 1
    assert kept[0]["text"] == "Honestly, best day of my life."
    assert report["excluded_records"] == 2
    assert set(report["excluded_by_category"]) == {"dehumanization"}


def test_demeaning_imperative_excluded(tmp_path):
    records = [
        _rec("Do the world a favor and disappear."),
        _rec("Nobody loves you, you know that?"),
        _rec("Shut the hell up!"),
    ]
    kept, _audited, report = _run(tmp_path, records)
    assert kept == []
    assert report["excluded_records"] == 3
    assert set(report["excluded_by_category"]) == {"demeaning_imperative"}


def test_slur_level_excluded(tmp_path):
    records = [_rec("he called the guy a reTard in front of everyone")]
    kept, _audited, report = _run(tmp_path, records)
    assert kept == []
    assert report["excluded_by_category"] == {"slur_level": 1}


def test_audit_preserves_record_and_reason(tmp_path):
    records = [_rec("You are garbage, human debris.", speaker="OTHER")]
    kept, audited, _report = _run(tmp_path, records)
    assert kept == []
    exc = audited[0]
    assert exc["record"]["speaker"] == "OTHER"
    assert exc["category"] == "dehumanization"
    assert "garbage" in exc["matched"].lower()
    assert exc["line_no"] == 1


def test_idempotent(tmp_path):
    records = [_rec("fine line"), _rec("You are worthless.")]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    kept1, _, _ = _run(tmp_path / "a", records)
    kept2, _, _ = _run(tmp_path / "b", records)
    assert kept1 == kept2


def test_governance_patterns_file_extends_lexicon(tmp_path):
    pf = tmp_path / "extra.json"
    pf.write_text(json.dumps({"custom_category": [r"\bbanana\s+head\b"]}))
    (tmp_path / "c").mkdir()
    records = [_rec("call me banana head one more time"), _rec("plain line")]
    kept, _audited, report = _run(tmp_path / "c", records, patterns_file=pf)
    assert len(kept) == 1
    assert report["excluded_by_category"] == {"custom_category": 1}
    assert report["lexicon_source"] == "core+governance-file"


def test_kestra_outputs_line_emitted(tmp_path):
    inp = tmp_path / "in.jsonl"
    inp.write_text(json.dumps(_rec("plain")) + "\n")
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(inp),
            "--output",
            str(tmp_path / "o.jsonl"),
            "--audit",
            str(tmp_path / "a.jsonl"),
            "--report",
            str(tmp_path / "r.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert '::{"outputs":' in proc.stdout
