from __future__ import annotations

import re

from huible.ingestion import Gate, GateContext, GateOutcome, GateResult

_KNOWN_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+(instructions|prompts)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.IGNORECASE),
    re.compile(r"system\s*:\s*", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|endoftext\|>", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"dANgErOuS", re.IGNORECASE),
    re.compile(r"(new\s+)?role\s*:\s*", re.IGNORECASE),
    re.compile(r"developer\s+mode", re.IGNORECASE),
    re.compile(r"act\s+as\s+(if|though|like)\s+", re.IGNORECASE),
]

_SUSPICIOUS_CONTENT_INDICATORS: list[re.Pattern[str]] = [
    re.compile(r"base64", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{32,}\b"),
    re.compile(r"\{.*\}.*\{.*\}", re.DOTALL),
    re.compile(r"(eval|exec|import)\s*\(", re.IGNORECASE),
]


class SafetyGate(Gate):
    name = "safety"

    def __init__(self, tier2_model=None, config: dict | None = None) -> None:
        self._tier2_model = tier2_model
        self._config = config or {}
        self._injection_threshold = self._config.get("injection_threshold", 2)
        self._suspicious_threshold = self._config.get("suspicious_threshold", 2)

    async def evaluate(self, candidate: dict, context: GateContext) -> GateResult:
        content = candidate.get("content", "")
        tier2 = self._tier2_model or context.tier2_model

        pattern_score = 0
        matched_patterns: list[str] = []

        for pattern in _KNOWN_INJECTION_PATTERNS:
            if pattern.search(content):
                pattern_score += 1
                matched_patterns.append(pattern.pattern)

        suspicious_score = 0
        for pattern in _SUSPICIOUS_CONTENT_INDICATORS:
            if pattern.search(content):
                suspicious_score += 1

        details = {
            "injection_matches": matched_patterns,
            "injection_score": pattern_score,
            "suspicious_score": suspicious_score,
        }

        if pattern_score >= self._injection_threshold:
            return GateResult(
                outcome=GateOutcome.FAIL,
                reason="Prompt injection detected",
                details=details,
            )

        if suspicious_score >= self._suspicious_threshold and pattern_score >= 1:
            return GateResult(
                outcome=GateOutcome.AMBIGUOUS,
                reason="Potentially adversarial content detected, needs review",
                details=details,
            )

        if tier2 is not None:
            try:
                tier2_result = await tier2("safety", candidate, context)
                if tier2_result is not None:
                    if tier2_result.get("outcome") == "fail":
                        return GateResult(
                            outcome=GateOutcome.FAIL,
                            reason=tier2_result.get("reason", "Safety violation (Tier 2)"),
                            details={**details, "tier2": tier2_result},
                        )
                    if tier2_result.get("outcome") == "ambiguous":
                        return GateResult(
                            outcome=GateOutcome.AMBIGUOUS,
                            reason=tier2_result.get("reason", "Ambiguous safety signal (Tier 2)"),
                            details={**details, "tier2": tier2_result},
                        )
            except Exception:
                pass

        return GateResult(
            outcome=GateOutcome.PASS,
            reason="No safety issues detected",
            details=details,
        )
