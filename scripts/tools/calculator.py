#!/usr/bin/env python3
"""
calculator — evaluate arithmetic without LLM hallucination.

Restrictive safe-eval: only numbers, + - * / ** % ( ) and named constants
pi, e, tau. No names, no imports, no attribute access. The SLM must never
"compute" a number itself — always call this tool.

Usage:
    python -m scripts.tools.calculator --expr "2 + 3 * 4"
    python -m scripts.tools.calculator --expr "sin(pi/4)"  # NOT supported, errors
    python -m scripts.tools.calculator --expr "sqrt(16)"   # NOT supported, errors

Output (JSON on stdout):
    {"expr": "2 + 3 * 4", "result": 14, "type": "int"}

Errors exit 1 with:
    {"expr": "...", "error": "..."}
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import operator
import sys
from collections.abc import Callable

# Whitelisted binary operators (ast.BinaryOperator -> callable)
_BIN_OPS: dict[type, Callable[[object, object], object]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# Whitelisted unary operators
_UNARY_OPS: dict[type, Callable[[object], object]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Named constants the calculator understands.
_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
}


def _eval_node(node: ast.AST) -> object:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise ValueError(f"unsupported binary operator: {op_type.__name__}")
        return _BIN_OPS[op_type](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ValueError(f"unsupported unary operator: {op_type.__name__}")
        return _UNARY_OPS[op_type](_eval_node(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ValueError(f"unknown name: {node.id!r}")
    raise ValueError(f"unsupported expression node: {type(node).__name__}")


def evaluate(expr: str) -> object:
    """Parse and evaluate a restricted arithmetic expression."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"parse error: {exc.msg}") from exc
    return _eval_node(tree)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate an arithmetic expression safely.")
    parser.add_argument("--expr", required=True, help="Expression to evaluate")
    args = parser.parse_args()

    try:
        result = evaluate(args.expr)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        print(json.dumps({"expr": args.expr, "error": str(exc)}))
        return 1

    # Normalize numeric type for JSON
    if isinstance(result, float) and result.is_integer():
        # Keep floatness if the expr used division; otherwise prefer int
        type_name = "float"
    elif isinstance(result, int):
        type_name = "int"
    else:
        type_name = type(result).__name__

    print(json.dumps({"expr": args.expr, "result": result, "type": type_name}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
