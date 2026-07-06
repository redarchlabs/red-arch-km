"""A small, self-contained JsonLogic evaluator (safe subset).

Workflow conditions are stored as JsonLogic expressions and evaluated in Python
against ``{"before": {...}, "after": {...}}``. Implementing the needed subset
here avoids a third-party dependency and keeps evaluation sandboxed: only the
whitelisted operators below run — there is no code execution or attribute access.
"""

from __future__ import annotations

from typing import Any


def _get_var(data: Any, path: Any, default: Any = None) -> Any:
    if path == "" or path is None:
        return data
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _truthy(value: Any) -> bool:
    # JsonLogic truthiness: [] and "" and 0 are falsy.
    if isinstance(value, list):
        return len(value) > 0
    return bool(value)


def json_logic(rule: Any, data: Any) -> Any:
    """Evaluate a JsonLogic ``rule`` against ``data``. Unknown operators raise."""
    if not isinstance(rule, dict) or len(rule) != 1:
        return rule  # literal (str, number, bool, list, None)

    op, raw = next(iter(rule.items()))
    values = raw if isinstance(raw, list) else [raw]

    if op == "var":
        return _get_var(data, json_logic(values[0], data) if values else "", values[1] if len(values) > 1 else None)
    if op == "missing":
        keys = values if not (len(values) == 1 and isinstance(values[0], list)) else values[0]
        return [k for k in keys if _get_var(data, k) is None]

    if op in ("and", "or"):
        result: Any = None
        for v in values:
            result = json_logic(v, data)
            if op == "and" and not _truthy(result):
                return result
            if op == "or" and _truthy(result):
                return result
        return result

    args = [json_logic(v, data) for v in values]

    match op:
        case "==":
            return _loose_eq(args[0], args[1])
        case "!=":
            return not _loose_eq(args[0], args[1])
        case "===":
            return args[0] == args[1]
        case "!==":
            return args[0] != args[1]
        case "!":
            return not _truthy(args[0])
        case "!!":
            return _truthy(args[0])
        case ">":
            return _cmp(args[0], args[1]) > 0
        case ">=":
            return _cmp(args[0], args[1]) >= 0
        case "<":
            return _cmp(args[0], args[1]) < 0
        case "<=":
            return _cmp(args[0], args[1]) <= 0
        case "in":
            container = args[1]
            return args[0] in container if container is not None else False
        case "+":
            return sum(_num(a) for a in args)
        case "-":
            return -_num(args[0]) if len(args) == 1 else _num(args[0]) - _num(args[1])
        case "*":
            product = 1.0
            for a in args:
                product *= _num(a)
            return product
        case "/":
            return _num(args[0]) / _num(args[1])
        case _:
            raise ValueError(f"unsupported JsonLogic operator: {op!r}")


def _loose_eq(a: Any, b: Any) -> bool:
    if type(a) is type(b):
        return a == b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return str(a) == str(b)


def _num(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value is None or value == "":
        return 0.0
    return float(value)


def _cmp(a: Any, b: Any) -> int:
    """Return -1/0/1 comparing a and b, coercing numeric-like operands."""
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        na, nb = _num(a), _num(b)
    else:
        na, nb = a, b  # type: ignore[assignment]
    if na < nb:
        return -1
    if na > nb:
        return 1
    return 0
