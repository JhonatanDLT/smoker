"""Turn a catalog and a spec file into verifiable invariants.

Every invariant knows how to find its own violations. That is the whole
contract: if it cannot produce a violation query, it is not an invariant.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .ir import Catalog, Column, Table, quote_ident, quote_qualified


class InvariantKind(str, Enum):
    NOT_NULL = "not_null"
    UNIQUE = "unique"
    FOREIGN_KEY = "foreign_key"
    CHECK = "check"
    ENUM = "enum"
    APP_RULE = "app_rule"
    EXCLUSION = "exclusion"
    TRIGGER = "trigger"


class InvariantStatus(str, Enum):
    DECLARED = "declared"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    SKIPPED = "skipped"


@dataclass
class Invariant:
    id: str
    kind: InvariantKind
    table: str
    description: str
    violation_sql: str
    status: InvariantStatus = InvariantStatus.DECLARED
    source: str = "catalog"
    columns: list[str] = field(default_factory=list)
    confidence: float = 1.0
    note: str | None = None

    @property
    def key(self) -> str:
        return self.id


_SEMANTIC_PATTERNS: list[tuple[str, str]] = [
    ("email", r"(^|_)(email|correo|mail|e_mail)($|_)"),
    ("phone", r"(phone|telefono|tel_|movil|celular|whatsapp)"),
    ("rfc", r"(^|_)rfc($|_)"),
    ("curp", r"(^|_)curp($|_)"),
    ("clabe", r"(^|_)clabe($|_)"),
    ("first_name", r"(first_name|nombres?|given_name)"),
    ("last_name", r"(last_name|apellidos?|surname|family_name)"),
    ("full_name", r"(^name$|full_name|nombre_completo|razon_social|business_name|company_name)"),
    ("address", r"(address|direccion|domicilio|calle)"),
    ("postal_code", r"(postal|zip_code|(^|_)cp($|_)|codigo_postal)"),
    ("city", r"(city|ciudad|municipio)"),
    ("region", r"(^state$|provincia|region)"),
    ("country", r"(country|pais)"),
    ("company", r"(^company$|empresa|organization|organizacion)"),
    ("url", r"(url|website|sitio_web|link|href)"),
    ("ip", r"(^ip$|ip_address|ipv4)"),
    ("amount", r"(amount|monto|total|price|precio|importe|saldo|balance|subtotal|cost|costo|fee)"),
    ("quantity", r"(quantity|qty|cantidad|^count$|numero)"),
    ("percentage", r"(percent|porcentaje|^rate$|tasa|pct)"),
    ("timestamp", r"(created_at|updated_at|deleted_at|timestamp|_at$)"),
    ("date", r"(date|fecha|^day$|^dia$)"),
    ("username", r"(username|user_name|login)"),
    ("slug", r"(slug|handle)"),
    ("code", r"(code|codigo|sku|clave|reference|^ref$)"),
    ("status", r"(status|^estado$|tipo|^type$|kind|category|categoria)"),
    ("description", r"(description|descripcion|notes|notas|comment|comentario|^body$)"),
    ("title", r"(title|titulo|subject|asunto)"),
]

_RANGE_RE = re.compile(
    r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)"
)
_IN_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s+IN\s*\(([^)]*)\)", re.IGNORECASE)
_ANY_RE = re.compile(
    r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*ANY\s*\(\s*(?:ARRAY\s*)?\[([^\]]*)\]",
    re.IGNORECASE,
)
_LEN_RE = re.compile(
    r"(?:char_length|length)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\)\s*<=\s*(\d+)",
    re.IGNORECASE,
)
_STRING_RE = re.compile(r"'((?:[^']|'')*)'")


@dataclass
class LocalRules:
    """Best-effort constraints the generator can enforce without the database."""

    ranges: dict[str, tuple[float | None, float | None, bool, bool]] = field(
        default_factory=dict
    )
    allowed: dict[str, set[str]] = field(default_factory=dict)
    maxlen: dict[str, int] = field(default_factory=dict)
    orderings: list[tuple[str, str, bool]] = field(default_factory=list)
    derived: list[tuple[str, str, str, str]] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    def apply_range(
        self,
        column: str,
        low: float | None,
        high: float | None,
        low_strict: bool = False,
        high_strict: bool = False,
    ) -> None:
        current_low, current_high, current_low_strict, current_high_strict = self.ranges.get(
            column, (None, None, False, False)
        )
        if low is not None:
            if current_low is None or low > current_low:
                current_low, current_low_strict = low, low_strict
            elif low == current_low:
                current_low_strict = current_low_strict or low_strict
        if high is not None:
            if current_high is None or high < current_high:
                current_high, current_high_strict = high, high_strict
            elif high == current_high:
                current_high_strict = current_high_strict or high_strict
        self.ranges[column] = (
            current_low,
            current_high,
            current_low_strict,
            current_high_strict,
        )


def infer_semantic(column: Column) -> str | None:
    if column.enum_values:
        return "enum"
    haystack = " ".join(part for part in (column.name, column.comment or "") if part).lower()
    for semantic, pattern in _SEMANTIC_PATTERNS:
        if re.search(pattern, haystack):
            return semantic
    return None


def extract_predicate(definition: str) -> str | None:
    """Pull the SQL predicate out of `CHECK ((x > 0))`."""
    text = definition.strip()
    if text.upper().endswith("NOT VALID"):
        text = text[: -len("NOT VALID")].strip()
    marker = text.upper().find("CHECK")
    if marker == -1:
        return None
    rest = text[marker + len("CHECK") :].strip()
    if not rest.startswith("("):
        return None
    depth = 0
    for idx, char in enumerate(rest):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return rest[1:idx].strip()
    return None


def parse_local_rules(table: Table) -> LocalRules:
    rules = LocalRules()
    for check in table.checks:
        predicate = check.predicate or extract_predicate(check.definition)
        if not predicate:
            rules.unparsed.append(check.name)
            continue
        matched = False
        for column, operator, raw_value in _RANGE_RE.findall(predicate):
            value = float(raw_value)
            if operator == ">":
                rules.apply_range(column, value, None, low_strict=True)
            elif operator == ">=":
                rules.apply_range(column, value, None)
            elif operator == "<":
                rules.apply_range(column, None, value, high_strict=True)
            else:
                rules.apply_range(column, None, value)
            matched = True
        for match in _IN_RE.findall(predicate):
            column, inside = match
            values = {value.replace("''", "'") for value in _STRING_RE.findall(inside)}
            if values:
                rules.allowed.setdefault(column, set()).update(values)
                matched = True
        for match in _ANY_RE.findall(predicate):
            column, inside = match
            values = {value.replace("''", "'") for value in _STRING_RE.findall(inside)}
            if values:
                rules.allowed.setdefault(column, set()).update(values)
                matched = True
        for column, limit in _LEN_RE.findall(predicate):
            rules.maxlen[column] = min(rules.maxlen.get(column, int(limit)), int(limit))
            matched = True
        known_columns = {column.name for column in table.columns}
        cast = r"(?:::[\w]+(?:\s+[\w]+)?)?"
        for target, left, operator, right in re.findall(
            rf"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\(*\s*([A-Za-z_][A-Za-z0-9_]*){cast}"
            rf"\s*([+\-*/])\s*([A-Za-z_][A-Za-z0-9_]*){cast}\s*\)*",
            predicate,
        ):
            if target in known_columns and left in known_columns and right in known_columns:
                rules.derived.append((target, left, operator, right))
                matched = True
        for left, operator, right in re.findall(
            rf"\b([A-Za-z_][A-Za-z0-9_]*){cast}\s*(>=|<=|>|<)\s*([A-Za-z_][A-Za-z0-9_]*){cast}\b",
            predicate,
        ):
            if left in known_columns and right in known_columns:
                if operator in (">", ">="):
                    rules.orderings.append((left, right, operator == ">"))
                else:
                    rules.orderings.append((right, left, operator == "<"))
                matched = True
        if not matched:
            rules.unparsed.append(check.name)
    return rules


def _q(schema: str, table: str) -> str:
    return quote_qualified(schema, table)


def _c(name: str) -> str:
    return quote_ident(name)


def _invariant_id(kind: str, table: Table, suffix: str) -> str:
    digest = hashlib.sha1(f"{kind}:{table.key}:{suffix}".encode()).hexdigest()[:10]
    return f"{kind}:{table.key}:{digest}"


def invariants_from_catalog(catalog: Catalog) -> list[Invariant]:
    invariants: list[Invariant] = []
    for table in catalog.tables:
        qualified = _q(table.schema, table.name)

        for column in table.columns:
            if not column.nullable:
                invariants.append(
                    Invariant(
                        id=_invariant_id("not_null", table, column.name),
                        kind=InvariantKind.NOT_NULL,
                        table=table.key,
                        columns=[column.name],
                        description=f"{table.key}.{column.name} is never null",
                        violation_sql=(
                            f"SELECT 1 AS violation FROM {qualified} "
                            f"WHERE {_c(column.name)} IS NULL"
                        ),
                    )
                )
            if column.enum_values:
                values = ", ".join(_sql_literal(v) for v in column.enum_values)
                invariants.append(
                    Invariant(
                        id=_invariant_id("enum", table, column.name),
                        kind=InvariantKind.ENUM,
                        table=table.key,
                        columns=[column.name],
                        description=(
                            f"{table.key}.{column.name} only takes "
                            f"{', '.join(column.enum_values)}"
                        ),
                        violation_sql=(
                            f"SELECT 1 AS violation FROM {qualified} "
                            f"WHERE {_c(column.name)} IS NOT NULL "
                            f"AND {_c(column.name)}::text NOT IN ({values})"
                        ),
                    )
                )

        if table.primary_key:
            columns = ", ".join(_c(name) for name in table.primary_key)
            not_null = " AND ".join(f"{_c(name)} IS NOT NULL" for name in table.primary_key)
            invariants.append(
                Invariant(
                    id=_invariant_id("unique", table, "pk"),
                    kind=InvariantKind.UNIQUE,
                    table=table.key,
                    columns=list(table.primary_key),
                    description=f"{table.key} primary key is unique",
                    violation_sql=(
                        f"SELECT {columns}, count(*) AS duplicates FROM {qualified} "
                        f"WHERE {not_null} GROUP BY {columns} HAVING count(*) > 1"
                    ),
                )
            )

        for unique in table.uniques:
            columns = ", ".join(_c(name) for name in unique.columns)
            not_null = " AND ".join(f"{_c(name)} IS NOT NULL" for name in unique.columns)
            invariants.append(
                Invariant(
                    id=_invariant_id("unique", table, unique.name),
                    kind=InvariantKind.UNIQUE,
                    table=table.key,
                    columns=list(unique.columns),
                    description=f"{table.key}({', '.join(unique.columns)}) is unique",
                    violation_sql=(
                        f"SELECT {columns}, count(*) AS duplicates FROM {qualified} "
                        f"WHERE {not_null} GROUP BY {columns} HAVING count(*) > 1"
                    ),
                )
            )

        for fk in table.foreign_keys:
            child_columns = ", ".join(_c(name) for name in fk.child_columns)
            conditions = " AND ".join(
                f"c.{_c(name)} IS NOT NULL" for name in fk.child_columns
            )
            joins = " AND ".join(
                f"p.{_c(parent)} = c.{_c(child)}"
                for child, parent in zip(fk.child_columns, fk.parent_columns)
            )
            invariants.append(
                Invariant(
                    id=_invariant_id("fk", table, fk.name),
                    kind=InvariantKind.FOREIGN_KEY,
                    table=table.key,
                    columns=list(fk.child_columns),
                    description=(
                        f"{table.key}({', '.join(fk.child_columns)}) references "
                        f"{fk.parent_key}({', '.join(fk.parent_columns)})"
                    ),
                    violation_sql=(
                        f"SELECT {child_columns} FROM {qualified} c "
                        f"WHERE {conditions} AND NOT EXISTS ("
                        f"SELECT 1 FROM {_q(fk.parent_schema, fk.parent_table)} p "
                        f"WHERE {joins})"
                    ),
                )
            )

        for check in table.checks:
            predicate = check.predicate or extract_predicate(check.definition)
            if predicate is None:
                invariants.append(
                    Invariant(
                        id=_invariant_id("check", table, check.name),
                        kind=InvariantKind.CHECK,
                        table=table.key,
                        description=f"{table.key} check {check.name}",
                        violation_sql="",
                        status=InvariantStatus.SKIPPED,
                        columns=[],
                        note=f"could not extract predicate from {check.definition!r}",
                    )
                )
                continue
            invariants.append(
                Invariant(
                    id=_invariant_id("check", table, check.name),
                    kind=InvariantKind.CHECK,
                    table=table.key,
                    columns=_columns_in_predicate(predicate),
                    description=f"{table.key} check {check.name}: {predicate}",
                    violation_sql=(
                        f"SELECT 1 AS violation FROM {qualified} "
                        f"WHERE ({predicate}) IS FALSE"
                    ),
                    note=None if check.validated else "constraint is NOT VALID on existing rows",
                )
            )

        for trigger in table.triggers:
            invariants.append(
                Invariant(
                    id=_invariant_id("trigger", table, trigger.name),
                    kind=InvariantKind.TRIGGER,
                    table=table.key,
                    description=f"{table.key} trigger {trigger.name} ({trigger.events})",
                    violation_sql="",
                    status=InvariantStatus.SKIPPED,
                    note=(
                        "trigger effects are not verified in v1; "
                        "run the reconciliation checks or add an app rule"
                    ),
                )
            )

    return invariants


def _columns_in_predicate(predicate: str) -> list[str]:
    candidates = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", predicate))
    return sorted(c for c in candidates if c.lower() not in _SQL_KEYWORDS)


_SQL_KEYWORDS = {
    "and",
    "or",
    "not",
    "is",
    "null",
    "true",
    "false",
    "in",
    "any",
    "all",
    "array",
    "between",
    "like",
    "ilike",
    "similar",
    "to",
    "cast",
    "as",
    "text",
    "integer",
    "numeric",
    "date",
    "timestamp",
    "interval",
    "current_date",
    "now",
    "length",
    "char_length",
}


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def merge_invariants(base: list[Invariant], extra: list[Invariant]) -> list[Invariant]:
    seen = {inv.id for inv in base}
    merged = list(base)
    for inv in extra:
        if inv.id in seen:
            continue
        merged.append(inv)
        seen.add(inv.id)
    return merged


def summarize(invariants: list[Invariant]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for inv in invariants:
        counts[inv.kind.value] = counts.get(inv.kind.value, 0) + 1
    return counts


def invariants_to_dict(invariants: list[Invariant]) -> list[dict[str, Any]]:
    return [
        {
            "id": inv.id,
            "kind": inv.kind.value,
            "table": inv.table,
            "description": inv.description,
            "status": inv.status.value,
            "source": inv.source,
            "columns": inv.columns,
            "confidence": inv.confidence,
            "note": inv.note,
            "violation_sql": inv.violation_sql,
        }
        for inv in invariants
    ]


def semantic_for_catalog(catalog: Catalog) -> None:
    for table in catalog.tables:
        for column in table.columns:
            if column.semantic is None:
                column.semantic = infer_semantic(column)
