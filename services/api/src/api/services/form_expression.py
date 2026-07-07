"""Sandboxed expression evaluation for ``calculated`` form elements.

A calculated element stores a JsonLogic-style expression (a dict/list AST or a
literal). Evaluation is a whitelisted-operator interpreter — there is **no**
code execution, attribute access, or import, so an author-supplied expression
can never escape the sandbox. The op set is intentionally small and mirrored by
the TypeScript port (``ui/src/lib/forms/jsonLogic.ts``) so a value previews
identically on the client and is recomputed authoritatively here on submit.

Date semantics are UTC and ISO-8601 to match the entity layer's coercion
(``repositories/dynamic_entity.py``). Any evaluation error yields ``None`` rather
than raising, so a bad formula degrades to an empty value instead of failing the
whole submission.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

# The unit vocabulary shared with the TS port.
_UNIT_DAYS = {"day": 1, "week": 7}
_MONTH_UNITS = {"month": 1, "year": 12}


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
    return None


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    # Clamp the day to the target month's length.
    for day in (d.day, 28, 29, 30, 31):
        try:
            return date(year, month, min(day, 31))
        except ValueError:
            continue
    return date(year, month, 28)


def _num(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return Decimal(1 if value else 0)
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str) and value.strip():
        try:
            return Decimal(value.strip())
        except InvalidOperation:
            return None
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) > 0
    return bool(value)


def _get_var(data: Any, path: Any) -> Any:
    if path in ("", None):
        return data
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def evaluate(expr: Any, context: dict[str, Any]) -> Any:
    """Evaluate a whitelisted expression AST against ``context`` (a flat map of
    the form's current values). Returns a Python scalar, or ``None`` on error."""
    try:
        return _eval(expr, context)
    except Exception:  # noqa: BLE001 - a bad formula must not sink a submission
        return None


def _eval(expr: Any, ctx: dict[str, Any]) -> Any:
    if not isinstance(expr, dict) or len(expr) != 1:
        return expr  # literal (str/number/bool/list/None)
    op, raw = next(iter(expr.items()))
    args = raw if isinstance(raw, list) else [raw]
    ev = [_eval(a, ctx) for a in args]

    if op == "var":
        return _get_var(ctx, ev[0] if ev else "")
    if op == "cat":
        return "".join("" if v is None else str(v) for v in ev)
    if op == "if":
        # if(cond, then, cond2, then2, ..., else)
        i = 0
        while i + 1 < len(ev):
            if _truthy(ev[i]):
                return ev[i + 1]
            i += 2
        return ev[i] if i < len(ev) else None
    if op in ("and",):
        result: Any = True
        for v in ev:
            if not _truthy(v):
                return v
            result = v
        return result
    if op in ("or",):
        for v in ev:
            if _truthy(v):
                return v
        return ev[-1] if ev else None
    if op == "!":
        return not _truthy(ev[0])
    if op in ("==", "!=", "<", "<=", ">", ">="):
        return _compare(op, ev)
    if op in ("+", "-", "*", "/"):
        return _arith(op, ev)
    if op == "today":
        return datetime.now(UTC).date().isoformat()
    if op == "now":
        return datetime.now(UTC).isoformat()
    if op == "date_add":
        return _date_add(ev)
    if op == "date_diff":
        return _date_diff(ev)
    raise ValueError(f"unknown operator: {op!r}")


def _compare(op: str, ev: list[Any]) -> bool:
    a, b = (ev + [None, None])[:2]
    na, nb = _num(a), _num(b)
    if na is not None and nb is not None:
        a, b = na, nb
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if a is None or b is None:
        return False
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    return a >= b


def _arith(op: str, ev: list[Any]) -> Any:
    nums = [_num(v) for v in ev]
    if any(n is None for n in nums) or not nums:
        return None
    acc = nums[0]
    for n in nums[1:]:
        if op == "+":
            acc += n
        elif op == "-":
            acc -= n
        elif op == "*":
            acc *= n
        else:
            if n == 0:
                return None
            acc /= n
    # Return an int when it is whole, else a float (JSON-friendly).
    if acc == acc.to_integral_value():
        return int(acc)
    return float(acc)


def _date_add(ev: list[Any]) -> str | None:
    if len(ev) < 3:
        return None
    base, amount_raw, unit = ev[0], ev[1], str(ev[2])
    d = _to_date(base)
    amount = _num(amount_raw)
    if d is None or amount is None:
        return None
    n = int(amount)
    if unit in _UNIT_DAYS:
        return (d + timedelta(days=n * _UNIT_DAYS[unit])).isoformat()
    if unit in _MONTH_UNITS:
        return _add_months(d, n * _MONTH_UNITS[unit]).isoformat()
    return None


def _date_diff(ev: list[Any]) -> int | None:
    if len(ev) < 2:
        return None
    a, b = _to_date(ev[0]), _to_date(ev[1])
    if a is None or b is None:
        return None
    return (a - b).days
