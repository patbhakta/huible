from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC
from enum import StrEnum
from pathlib import Path


class CriterionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class CriterionResult:
    criterion: str
    status: CriterionStatus
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    total_tests: int = 0
    details: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


GATE_SPECS: dict[str, list[tuple[str, str]]] = {
    "f1": [
        ("test_f1_1_indexing.py", "F1.1"),
        ("test_f1_2_spreading_activation.py", "F1.2"),
        ("test_f1_3_feedback_suppression.py", "F1.3"),
        ("test_f1_4_disclosure_scoping.py", "F1.4"),
        ("test_f1_5_motif_escalation.py", "F1.5"),
    ],
    "f2": [
        ("test_f2_1_safety_gate.py", "F2.1"),
        ("test_f2_2_deduplication_gate.py", "F2.2"),
        ("test_f2_3_novelty_gate.py", "F2.3"),
        ("test_f2_4_immutability_gate.py", "F2.4"),
        ("test_f2_5_pertinence_gate.py", "F2.5"),
        ("test_f2_6_pipeline_integration.py", "F2.6"),
    ],
    "f3": [
        ("test_f3_quarantine.py", "F3"),
    ],
    "f4": [
        ("test_f4_versioning.py", "F4"),
    ],
    "f5": [
        ("test_f5_context_builder.py", "F5"),
    ],
    "f6": [
        ("test_f6_ingestion_pipeline.py", "F6"),
    ],
    "f7": [
        ("test_f7_disclosure.py", "F7"),
    ],
    "f8": [
        ("test_f8_benchmarks.py", "F8"),
    ],
}

ALL_MANDATORY = [
    "F1.1", "F1.2", "F1.3", "F1.4", "F1.5",
    "F2.1", "F2.2", "F2.3", "F2.4", "F2.5", "F2.6",
    "F3", "F4", "F5", "F6", "F7", "F8",
]


@dataclass
class GateReport:
    criteria: dict[str, CriterionResult] = field(default_factory=dict)
    total_elapsed_ms: float = 0.0
    overall_status: str = "PENDING"
    timestamp: str = ""

    def set_criterion(self, result: CriterionResult) -> None:
        self.criteria[result.criterion] = result

    def compute_overall(self) -> str:
        fail_default = CriterionResult(criterion="", status=CriterionStatus.FAIL)
        all_pass = all(
            self.criteria.get(c, fail_default).status == CriterionStatus.PASS
            for c in ALL_MANDATORY
        )
        return "PASS" if all_pass else "FAIL"

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append("# F1-F8 Gate Report — Huible Engine Executable Spec")
        lines.append("")
        lines.append(f"**Overall: {self.compute_overall()}**")
        lines.append(f"**Timestamp:** {self.timestamp}")
        lines.append(f"**Total time:** {self.total_elapsed_ms:.0f}ms")
        lines.append("")
        lines.append("## Criteria")
        lines.append("")
        lines.append("| Criterion | Status | Passed | Failed | Skipped | Time |")
        lines.append("|-----------|--------|--------|--------|---------|------|")
        for c in sorted(self.criteria.keys()):
            r = self.criteria[c]
            row = (
                f"| {c} | **{r.status}** | {r.tests_passed} | "
                f"{r.tests_failed} | {r.tests_skipped} | {r.elapsed_ms:.0f}ms |"
            )
            lines.append(row)
        lines.append("")
        for c in sorted(self.criteria.keys()):
            r = self.criteria[c]
            if r.details:
                lines.append(f"### {c} Details")
                lines.append("")
                for d in r.details:
                    lines.append(f"- {d}")
                lines.append("")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({
            "overall": self.compute_overall(),
            "timestamp": self.timestamp,
            "total_elapsed_ms": self.total_elapsed_ms,
            "criteria": {
                k: {
                    "status": v.status,
                    "tests_passed": v.tests_passed,
                    "tests_failed": v.tests_failed,
                    "tests_skipped": v.tests_skipped,
                    "total_tests": v.total_tests,
                    "elapsed_ms": v.elapsed_ms,
                    "details": v.details,
                }
                for k, v in self.criteria.items()
            },
        }, indent=2)


def generate_report(
    output_path: str | Path | None = None,
    gates: list[str] | None = None,
) -> str:
    import subprocess
    from datetime import datetime

    project_root = Path(__file__).resolve().parent.parent
    report = GateReport(timestamp=datetime.now(UTC).isoformat())
    total_start = time.perf_counter()

    for gate_key, criterion_map in GATE_SPECS.items():
        if gates and gate_key not in gates:
            continue
        tests_dir = project_root / "tests" / gate_key

        for test_file, criterion in criterion_map:
            test_path = tests_dir / test_file
            if not test_path.exists():
                report.set_criterion(CriterionResult(
                    criterion=criterion, status=CriterionStatus.FAIL,
                    details=[f"Test file not found: {test_file}"],
                ))
                continue

            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path), "-v", "--tb=short", "-q"],
                capture_output=True, text=True,
                cwd=str(project_root), timeout=120,
            )

            output = result.stdout + result.stderr
            passed = output.count(" PASSED")
            failed = output.count(" FAILED")
            skipped = output.count(" SKIPPED")

            status = CriterionStatus.PASS
            if failed > 0:
                status = CriterionStatus.FAIL
            elif skipped > 0 and passed == 0:
                status = CriterionStatus.SKIP

            elapsed = 0.0
            for line in output.splitlines():
                if "in" in line.lower() and ("=" in line or line.strip().startswith("=")):
                    parts = line.split()
                    for p in parts:
                        try:
                            if p.endswith("s"):
                                elapsed = float(p[:-1]) * 1000
                                break
                        except ValueError:
                            continue

            details: list[str] = []
            for line in output.splitlines():
                if "PASSED" in line or "FAILED" in line or "SKIPPED" in line:
                    details.append(line.strip()[:120])

            report.set_criterion(CriterionResult(
                criterion=criterion, status=status,
                tests_passed=passed, tests_failed=failed,
                tests_skipped=skipped, total_tests=passed + failed + skipped,
                elapsed_ms=elapsed, details=details[-10:],
            ))

    report.total_elapsed_ms = (time.perf_counter() - total_start) * 1000
    report.overall_status = report.compute_overall()

    markdown = report.to_markdown()
    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(markdown)
        json_path = p.with_suffix(".json")
        json_path.write_text(report.to_json())

    return markdown


if __name__ == "__main__":
    output = sys.argv[1] if len(sys.argv) > 1 else None
    gates = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    print(generate_report(output, gates))
