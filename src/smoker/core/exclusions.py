"""Exclusion constraints: conflict detection and row adjustment.

An exclusion constraint is a table constraint: it bounds the generation space.
Two rows conflict when every ``column WITH operator`` element evaluates to true
for the pair, and both rows satisfy the optional ``WHERE`` predicate.

This module is pure: it parses literals and ranges, evaluates the predicate on a
row dict, detects conflicts against previously accepted rows, and proposes a
deterministic next value for the column the generator should adjust.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from .ir import Column, DataKind, Table

SUPPORTED_OPERATORS = {"=", "<>", "!=", "<", "<=", ">", ">=", "&&", "@>", "<@"}

_BASE_DATE = date(2025, 1, 1)
_BASE_DATETIME = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

_SQL_WORDS = {
    "and", "or", "not", "is", "null", "true", "false", "in", "between", "like",
    "ilike", "distinct", "from", "as", "case", "when", "then", "else", "end",
    "current_date", "current_time", "current_timestamp", "localtime",
    "localtimestamp", "interval", "year", "month", "day", "hour", "minute",
    "second", "days", "hours", "minutes", "seconds", "weeks", "months", "years",
}


@dataclass(frozen=True)
class ElementSpec:
    column: str
    operator: str
    kind: DataKind
    sql_type: str


@dataclass
class ExclusionSpec:
    name: str
    elements: list[ElementSpec]
    predicate: str | None = None
    supported: bool = True

    @property
    def columns(self) -> list[str]:
        return [element.column for element in self.elements]

    @property
    def equality_columns(self) -> list[str]:
        return [
            element.column for element in self.elements if element.operator == "="
        ]

    @property
    def range_columns(self) -> list[str]:
        return [
            element.column for element in self.elements if element.operator == "&&"
        ]


def parse_exclusion_definition(
    definition: str,
) -> tuple[str | None, list[tuple[str, str]], str | None]:
    """Parse ``EXCLUDE USING method (col WITH op, ...) WHERE (pred)``.

    Returns ``(method, elements, predicate)``. Elements that are expressions
    rather than plain columns are skipped; callers can fall back to catalog
    column numbers.
    """
    method_match = re.search(r"\bUSING\s+([A-Za-z_][A-Za-z0-9_]*)", definition, re.IGNORECASE)
    method = method_match.group(1) if method_match else None

    start = definition.find("(")
    if start == -1:
        return method, [], None
    depth = 0
    end: int | None = None
    for index in range(start, len(definition)):
        char = definition[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        return method, [], None

    body = definition[start + 1 : end]
    remainder = definition[end + 1 :].strip()
    predicate = (
        re.sub(r"^WHERE\s+", "", remainder, flags=re.IGNORECASE).strip() or None
        if remainder
        else None
    )
    elements: list[tuple[str, str]] = []
    for item in _split_top_level(body, ","):
        match = re.match(
            r'\s*("(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*)\s+WITH\s+(\S+)\s*$',
            item,
            re.IGNORECASE,
        )
        if not match:
            continue
        column = match.group(1).strip('"').replace('""', '"')
        elements.append((column, match.group(2)))
    return method, elements, predicate


def _unique_keys(table: Table) -> list[set[str]]:
    keys = [set(table.primary_key)] if table.primary_key else []
    keys.extend(set(unique.columns) for unique in table.uniques if unique.columns)
    return keys


def build_exclusion_specs(table: Table) -> list[ExclusionSpec]:
    """Return the exclusions that actually need generation-time enforcement."""
    unique_keys = _unique_keys(table)
    specs: list[ExclusionSpec] = []
    for exclusion in table.exclusions:
        elements: list[ElementSpec] = []
        for element in exclusion.elements:
            try:
                column = table.column(element.column)
            except KeyError:
                continue
            elements.append(
                ElementSpec(
                    column=element.column,
                    operator=element.operator.strip(),
                    kind=column.data_kind or DataKind.OTHER,
                    sql_type=column.sql_type,
                )
            )
        if not elements:
            continue
        supported = all(
            element.operator in SUPPORTED_OPERATORS for element in elements
        )
        equality = {
            element.column for element in elements if element.operator == "="
        }
        implied = bool(equality) and any(
            equality >= key for key in unique_keys if key
        )
        if implied:
            continue
        specs.append(
            ExclusionSpec(
                name=exclusion.name,
                elements=elements,
                predicate=exclusion.predicate,
                supported=supported,
            )
        )
    return specs


def _normalize_expression(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = re.sub(r"::[A-Za-z_][A-Za-z0-9_. ]*", "", text)
    return _strip_outer_parens(text)


def predicate_columns(predicate: str | None) -> set[str]:
    if not predicate:
        return set()
    cleaned = re.sub(r"'(?:[^']|'')*'", "''", _normalize_expression(predicate))
    found: set[str] = set()
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", cleaned):
        name = match.group(1)
        if name.lower() not in _SQL_WORDS:
            found.add(name)
    return found


def _strip_outer_parens(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        balanced = True
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        text = text[1:-1].strip()
    return text


def _split_top_level(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_string = False
    index = 0
    lowered = separator.lower()
    while index < len(text):
        char = text[index]
        if char == "'":
            if in_string and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            in_string = not in_string
            index += 1
            continue
        if not in_string:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0 and text[index : index + len(separator)].lower() == lowered:
                parts.append(text[:index].strip())
                text = text[index + len(separator) :]
                index = 0
                continue
        index += 1
    parts.append(text.strip())
    return parts


def _literal(text: str) -> Any:
    text = _strip_outer_parens(text.strip())
    upper = text.upper()
    if upper == "NULL":
        return None
    if upper == "TRUE":
        return True
    if upper == "FALSE":
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return Decimal(text)
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    return text


def _coerce(value: Any, target: Any) -> Any:
    if value is None or target is None:
        return target
    if isinstance(value, (date, datetime)) and isinstance(target, str):
        try:
            if isinstance(value, datetime):
                return datetime.fromisoformat(target.replace(" ", "T"))
            return date.fromisoformat(target)
        except ValueError:
            return target
    if isinstance(value, (int, float, Decimal)) and isinstance(target, str):
        try:
            return Decimal(target)
        except Exception:  # noqa: BLE001 - keep the raw literal
            return target
    return target


def _compare(value: Any, operator: str, target: Any) -> bool:
    target = _coerce(value, target)
    try:
        if operator == "=":
            return value == target
        if operator in ("<>", "!="):
            return value != target
        if operator == "<":
            return value < target
        if operator == "<=":
            return value <= target
        if operator == ">":
            return value > target
        if operator == ">=":
            return value >= target
    except TypeError:
        return False
    return False


def predicate_matches(predicate: str | None, values: dict[str, Any]) -> bool:
    """Evaluate the simple predicates used by exclusion constraints."""
    if not predicate:
        return True
    text = _normalize_expression(predicate)
    if not text:
        return True

    if text.upper().startswith("NOT "):
        return not predicate_matches(text[4:], values)

    for separator in (" OR ", " AND "):
        if separator.strip().lower() in text.lower():
            if "between" in text.lower():
                # BETWEEN ... AND ... is not split safely; enforce globally.
                return True
            parts = _split_top_level(text, separator)
            if len(parts) > 1:
                results = [predicate_matches(part, values) for part in parts]
                return any(results) if separator == " OR " else all(results)

    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+(NOT\s+)?NULL$", text, re.IGNORECASE)
    if match:
        value = values.get(match.group(1))
        return (value is not None) if match.group(2) else (value is None)

    match = re.match(
        r"^([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+(NOT\s+)?(TRUE|FALSE)$",
        text,
        re.IGNORECASE,
    )
    if match:
        value = values.get(match.group(1))
        expected = match.group(3).upper() == "TRUE"
        if match.group(2):
            return value is not expected
        return value is expected

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        return bool(values.get(text))

    match = re.match(
        r"^([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|<>|!=|=|<|>)\s*(.+)$",
        text,
        re.DOTALL,
    )
    if match:
        value = values.get(match.group(1))
        target = _literal(match.group(3))
        if value is None or target is None:
            return False
        return _compare(value, match.group(2), target)

    # Unknown predicate: enforce globally rather than risk a violation.
    return True


def parse_range(value: Any) -> tuple[Any, Any, bool, bool] | None:
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return value[0], value[1], True, False
    if not isinstance(value, str):
        return None
    match = re.match(
        r"^\s*([\[\(])\s*(.*?)\s*,\s*(.*?)\s*([\]\)])\s*$", value, re.DOTALL
    )
    if not match:
        return None
    lower = match.group(2) or None
    upper = match.group(3) or None
    return lower, upper, match.group(1) == "[", match.group(4) == "]"


def range_subtype(sql_type: str) -> str:
    kind = sql_type.lower().strip()
    if kind in {"int4range", "int8range"}:
        return "int"
    if kind == "numrange":
        return "numeric"
    if kind == "daterange":
        return "date"
    if kind in {"tsrange", "tstzrange"}:
        return "timestamp"
    return "text"


def _coerce_bound(value: Any, subtype: str) -> Any:
    if value is None:
        return None
    if subtype == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if subtype == "numeric":
        try:
            return Decimal(str(value))
        except Exception:  # noqa: BLE001 - an unparseable bound is not comparable
            return None
    if subtype == "date":
        try:
            return date.fromisoformat(str(value))
        except ValueError:
            return None
    if subtype == "timestamp":
        try:
            return datetime.fromisoformat(str(value).replace(" ", "T"))
        except ValueError:
            return None
    return str(value)


def ranges_overlap(left: Any, right: Any, sql_type: str) -> bool:
    first = parse_range(left)
    second = parse_range(right)
    if first is None or second is None:
        return False
    subtype = range_subtype(sql_type)
    a_low, a_high, a_low_inc, a_high_inc = first
    b_low, b_high, b_low_inc, b_high_inc = second
    a_low = _coerce_bound(a_low, subtype)
    a_high = _coerce_bound(a_high, subtype)
    b_low = _coerce_bound(b_low, subtype)
    b_high = _coerce_bound(b_high, subtype)

    def high_after_low(high: Any, high_inc: bool, low: Any, low_inc: bool) -> bool:
        if high is None or low is None:
            return True
        if high > low:
            return True
        return high == low and high_inc and low_inc

    def low_before_high(low: Any, low_inc: bool, high: Any, high_inc: bool) -> bool:
        if low is None or high is None:
            return True
        if low < high:
            return True
        return low == high and low_inc and high_inc

    return high_after_low(a_high, a_high_inc, b_low, b_low_inc) and low_before_high(
        a_low, a_low_inc, b_high, b_high_inc
    )


def element_matches(
    left: Any, right: Any, operator: str, kind: DataKind, sql_type: str
) -> bool:
    if left is None or right is None:
        return False
    if operator == "&&":
        if kind == DataKind.RANGE:
            return ranges_overlap(left, right, sql_type)
        if isinstance(left, (list, tuple, set)) and isinstance(
            right, (list, tuple, set)
        ):
            return bool(set(left) & set(right))
        return False
    if operator in {"@>", "<@"}:
        if kind == DataKind.RANGE:
            first = parse_range(left)
            second = parse_range(right)
            if first is None or second is None:
                return False
            if operator == "@>":
                return ranges_overlap(left, right, sql_type) and _covers(
                    first, second, sql_type
                )
            return ranges_overlap(left, right, sql_type) and _covers(
                second, first, sql_type
            )
        if isinstance(left, (list, tuple, set)) and isinstance(
            right, (list, tuple, set)
        ):
            return set(right) <= set(left) if operator == "@>" else set(left) <= set(right)
        return False
    if kind == DataKind.RANGE and operator == "=":
        first = parse_range(left)
        second = parse_range(right)
        return _covers(first, second, sql_type) and _covers(second, first, sql_type)
    return _compare(left, operator, right)


def _covers(
    first: tuple[Any, Any, bool, bool] | None,
    second: tuple[Any, Any, bool, bool] | None,
    sql_type: str,
) -> bool:
    """Return True when range ``first`` contains range ``second``."""
    if first is None or second is None:
        return False
    subtype = range_subtype(sql_type)
    a_low, a_high, a_low_inc, a_high_inc = first
    b_low, b_high, b_low_inc, b_high_inc = second
    a_low = _coerce_bound(a_low, subtype)
    a_high = _coerce_bound(a_high, subtype)
    b_low = _coerce_bound(b_low, subtype)
    b_high = _coerce_bound(b_high, subtype)

    lower_ok = a_low is None or (
        b_low is not None
        and (a_low < b_low or (a_low == b_low and (a_low_inc or not b_low_inc)))
    )
    upper_ok = a_high is None or (
        b_high is not None
        and (a_high > b_high or (a_high == b_high and (a_high_inc or not b_high_inc)))
    )
    return lower_ok and upper_ok


def first_conflict(
    specs: list[ExclusionSpec],
    values: dict[str, Any],
    history: list[dict[str, Any]],
) -> tuple[ExclusionSpec, dict[str, Any]] | None:
    for spec in specs:
        if not spec.supported:
            continue
        if not predicate_matches(spec.predicate, values):
            continue
        for previous in history:
            if not predicate_matches(spec.predicate, previous):
                continue
            if all(
                element_matches(
                    values.get(element.column),
                    previous.get(element.column),
                    element.operator,
                    element.kind,
                    element.sql_type,
                )
                for element in spec.elements
            ):
                return spec, previous
    return None


def next_value(
    column: Column,
    current: Any,
    index: int,
    attempt: int,
    seed: int,
) -> Any:
    kind = column.data_kind
    step = 1 + max(0, int(attempt))
    if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
        return int(current or 0) + step
    if kind == DataKind.NUMERIC:
        return Decimal(str(current or 0)) + step
    if kind == DataKind.FLOAT:
        return float(current or 0) + float(step)
    if kind == DataKind.BOOLEAN:
        return not bool(current)
    if kind == DataKind.DATE:
        return (current or _BASE_DATE) + timedelta(days=step)
    if kind in (DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
        value = current or _BASE_DATETIME
        return value + timedelta(seconds=step)
    if kind == DataKind.TIME:
        value = current or time(9, 0, 0)
        return (
            datetime.combine(date(2000, 1, 1), value) + timedelta(seconds=step)
        ).time()
    if kind == DataKind.UUID:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{seed}:exclusion:{column.name}:{index}:{attempt}",
            )
        )
    if kind == DataKind.ENUM:
        values = column.enum_values
        if not values:
            return None
        try:
            position = values.index(current)
        except ValueError:
            return values[0]
        return values[position + 1] if position + 1 < len(values) else None
    if kind == DataKind.TEXT:
        suffix = f"-{index + 1}-{attempt}"
        limit = column.max_length or 64
        base = str(current or column.name)
        return (base[: max(0, limit - len(suffix))] + suffix)[:limit]
    return None


def _format_bound(value: Any, subtype: str) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def next_range(
    column: Column,
    current: Any,
    previous: Any,
) -> str | None:
    subtype = range_subtype(column.sql_type)
    previous_range = parse_range(previous)
    if previous_range is None:
        return None
    _low, high, _low_inc, _high_inc = previous_range
    high = _coerce_bound(high, subtype)
    if high is None:
        return None
    current_range = parse_range(current)
    width: Any
    if current_range is not None:
        low_current, high_current, _, _ = current_range
        low_current = _coerce_bound(low_current, subtype)
        high_current = _coerce_bound(high_current, subtype)
        if low_current is not None and high_current is not None:
            width = high_current - low_current
        else:
            width = None
    else:
        width = None
    if width is None:
        width = _default_width(subtype)
    lower = high
    upper = lower + width
    return f"[{_format_bound(lower, subtype)},{_format_bound(upper, subtype)})"


def _default_width(subtype: str) -> Any:
    if subtype in {"int", "numeric"}:
        return 10
    if subtype == "date":
        return timedelta(days=1)
    if subtype == "timestamp":
        return timedelta(hours=1)
    return Decimal(1)
