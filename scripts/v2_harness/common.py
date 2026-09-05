"""Shared plumbing for the v2 validation harness (HU-2706).

Reuses the HU-2472 W6 E0-replay rig (client, consent, retry, frozen turns) —
extend, don't rebuild. Adds corpus measurement (baselines are MEASURED from
the reference corpus at run time, never invented) and transcript archival.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.e0_replay_w6 import (  # noqa: E402  (W6 rig reuse — HU-2472)
    E0_USER_TURNS,
    PERSONA,
    consented_conv,
    log,
    request,
    resolve_key,
    turn_with_retry,
)

CORPUS_CSV = REPO_ROOT / "onboarding/Chandler Bing - FRIENDS sitcom/friends-v2.csv"
CHANDLER_PERSON = "chandler"  # CSV person value; 8,376 Chandler lines (measured)
CHANDLER_CORPUS_LINES = 8376  # measured 2026-09-04 (W6); re-measured each run
REPLY_CAP_CHARS = 400  # corpus p99 = 305 + headroom (same cap as the W6 rig)

FAKE_LLM_MARKER = "[fake-llm"

#: Assistant-speak register markers (M-0 "seemed more AI" verdict). The
#: reference corpus is human dialogue — the persona voice must not contain
#: these; corpus counts are measured per run and recorded next to probe hits.
ASSISTANT_SPEAK_MARKERS = [
    "as an ai",
    "i'm here to",
    "i am here to",
    "how can i help",
    "how may i help",
    "i'd be happy to",
    "i would be happy to",
    "certainly!",
    "of course!",
    "feel free to",
    "let me know if",
    "is there anything else",
    "i apologize",
    "i cannot assist",
    "i'm unable to",
    "sure thing",
    "hope this helps",
]

CODE_FLUENCY_MARKERS = [
    "import ",
    "sys.stdout",
    "print(",
    "printf",
    "def ",
    "```",
    "std::",
    "cout",
    "puts(",
    "console.log",
]

#: User-side patterns that reference shared past/events — a turn matching
#: these is memory-relevant for the H3 ledger; an empty retrieval there is
#: an ungrounded-injection risk (M-0 E4 failure class), not smalltalk.
MEMORY_RELEVANT_PATTERN = re.compile(
    r"\b(remember|recall|yesterday|last (time|week|night|year)|earlier|"
    r"first thing (i|you)|before|again|that time|we (talked|said)|i told you|"
    r"you told me|what did i (say|say))\b",
    re.I,
)

QUESTION_SENTENCE = re.compile(r"[^.!?]*\?")
# Second-person-directed speech, incl. corpus-attested contractions
# ("whatcha" — friends-v2.csv 1 Chandler line; "watcha" spelling variant).
SECOND_PERSON = re.compile(
    r"\b(you|your|you're|you've|u|ur|yours|whatcha|watcha)\b", re.I
)


def corpus_lines() -> list[str]:
    rows: list[str] = []
    with CORPUS_CSV.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if (row.get("person") or "").strip().lower() == CHANDLER_PERSON:
                rows.append((row.get("line") or "").strip())
    return rows


def measure_corpus_baselines() -> dict:
    """MEASURED corpus baselines — recomputed from the reference corpus every
    run so no number in the evidence is ever invented."""
    lines = corpus_lines()
    n = len(lines)
    q = sum(1 for ln in lines if "?" in ln)
    lens = sorted(len(ln) for ln in lines)
    p99 = lens[min(n - 1, int(n * 0.99))]
    marker_counts = {
        m: sum(1 for ln in lines if m in ln.lower()) for m in ASSISTANT_SPEAK_MARKERS
    }
    code_counts = {
        m: sum(1 for ln in lines if m in ln.lower()) for m in CODE_FLUENCY_MARKERS
    }
    return {
        "corpus_csv": str(CORPUS_CSV.relative_to(REPO_ROOT)),
        "chandler_lines": n,
        "question_ratio": round(q / n, 4),
        "question_lines": q,
        "length_p99_chars": p99,
        "reply_cap_chars": REPLY_CAP_CHARS,
        "reply_cap_derivation": "corpus p99 + headroom (W6 rig cap, unchanged)",
        "assistant_speak_marker_counts": marker_counts,
        "code_marker_counts": code_counts,
    }


def question_band(n_replies: int, question_ratio: float) -> tuple[int, int]:
    """Binomial 95% band for expected question-line count in n replies."""
    import math

    mean = n_replies * question_ratio
    sd = math.sqrt(n_replies * question_ratio * (1 - question_ratio))
    lo = max(0, math.floor(mean - 1.96 * sd))
    hi = math.ceil(mean + 1.96 * sd)
    return lo, hi


def markers_in(reply: str, markers: list[str]) -> list[str]:
    low = reply.lower()
    return [m for m in markers if m in low]


def followup_questions(reply: str) -> list[str]:
    return [
        s.strip()[:160]
        for s in QUESTION_SENTENCE.findall(reply)
        if s.strip() and SECOND_PERSON.search(s)
    ]


def assert_live_reply(reply: str, where: str) -> None:
    """A fake-llm fallback reply is NOT persona evidence (W6 run-5 lesson):
    fail loudly instead of grading generator noise."""
    if FAKE_LLM_MARKER in reply:
        raise SystemExit(
            f"HARNESS INVALID: fake-llm fallback served at {where} — "
            "token ceiling/provider outage; this run is not persona evidence. "
            "Re-run on a live generator."
        )


def archive(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))
    return path


def archive_markdown(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
