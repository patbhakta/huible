"""
Grounded-truth tools for the HUible SLM.

These scripts are intentionally dependency-free and deterministic: the SLM
calls them via subprocess so it cannot hallucinate values that must be exact
(current date/time, the caller's location, or arithmetic). This is the
"anti-Pinocchio" layer — verified outputs, never generated text.

Each tool:
- Reads inputs from argv flags or stdin JSON (so it works as both a CLI and a
  Kestra task).
- Prints a single JSON object on stdout (parsed by the caller).
- Exits 0 on success, 1 on bad input, 2 on internal error.

Run directly to see usage:
    python -m scripts.tools.get_date --help
    python -m scripts.tools.get_time --help
    python -m scripts.tools.get_location --help
    python -m scripts.tools.calculator --help
"""

__all__ = ["calculator", "get_date", "get_location", "get_time"]
