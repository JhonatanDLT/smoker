"""Core data model for pgproof.

Everything in this package is pure: no database drivers, no I/O, no CLI.
Adapters translate a dialect into this model; the engine never looks back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any


class DataKind(str, Enum):
    SMALLINT = "smallint"
    INT = "int"
    BIGINT = "bigint"
    NUMERIC = "numeric"
    FLOAT = "float"
    BOOLEAN = "boolean"
    TEXT = "text"
    UUID = "uuid"
    DATE = "date"
    TIMESTAMP = "timestamp"
    TIMESTAMPTZ = "timestamptz"
    TIME = "time"
    JSON = "json"
    BYTEA = "bytea"
    ENUM = "enum"
    INET = "inet"
    ARRAY = "array"
    OTHER = "other"


_INT_TYPES = {"smallint", "int2", "integer", "int", "int4", "serial", "serial4"}
_BIGINT_TYPES = {"bigint", "int8", "bigserial", "serial8"}
_FLOAT_TYPES = {"real", "float4", "double precision", "float8"}


def parse_max_length(sql_type: str) -> int | None:
    match = re.search(r"\((\d+)\)", sql_type)
    return int(match.group(1)) if match else None


def classify_type(sql_type: str, enum_values: list[str] | None = None) -> DataKind:
    if enum_values:
        return DataKind.ENUM
    t = sql_type.lower().strip()
    if t.endswith("[]"):
        return DataKind.ARRAY
    if t in _INT_TYPES:
        return DataKind.SMALLINT if t in {"smallint", "int2"} else DataKind.INT
    if t in _BIGINT_TYPES:
        return DataKind.BIGINT
    if t.startswith(("numeric", "decimal")) or t == "money":
        return DataKind.NUMERIC
    if t in _FLOAT_TYPES:
        return DataKind.FLOAT
    if t in {"boolean", "bool"}:
        return DataKind.BOOLEAN
    if t.startswith(("character varying", "varchar", "character(", "char(")) or t == "text":
        return DataKind.TEXT
    if t == "uuid":
        return DataKind.UUID
    if t == "date":
        return DataKind.DATE
    if t.startswith("timestamp with time zone") or t == "timestamptz":
        return DataKind.TIMESTAMPTZ
    if t.startswith("timestamp"):
        return DataKind.TIMESTAMP
    if t.startswith("time"):
        return DataKind.TIME
    if t in {"json", "jsonb"}:
        return DataKind.JSON
    if t == "bytea":
        return DataKind.BYTEA
    if t in {"inet", "cidr"}:
        return DataKind.INET
    return DataKind.OTHER


@dataclass
class Column:
    name: str
    sql_type: str
    nullable: bool = True
    default: str | None = None
    identity: str | None = None
    generated: str | None = None
    enum_values: list[str] = field(default_factory=list)
    comment: str | None = None
    data_kind: DataKind | None = None
    semantic: str | None = None
    max_length: int | None = None

    def __post_init__(self) -> None:
        if self.data_kind is None:
            self.data_kind = classify_type(self.sql_type, self.enum_values)
        if self.max_length is None:
            self.max_length = parse_max_length(self.sql_type)

    @property
    def writable(self) -> bool:
        """Generated columns are computed by the database; never write them."""
        return self.generated is None


@dataclass
class ForeignKey:
    name: str
    child_schema: str
    child_table: str
    child_columns: list[str]
    parent_schema: str
    parent_table: str
    parent_columns: list[str]
    on_delete: str | None = None

    @property
    def parent_key(self) -> str:
        return f"{self.parent_schema}.{self.parent_table}"

    @property
    def child_key(self) -> str:
        return f"{self.child_schema}.{self.child_table}"


@dataclass
class UniqueConstraint:
    name: str
    columns: list[str]


@dataclass
class CheckConstraint:
    name: str
    definition: str
    predicate: str | None = None
    validated: bool = True


@dataclass
class Trigger:
    name: str
    timing: str
    events: str
    function: str
    definition: str
    body: str | None = None


@dataclass
class Table:
    schema: str
    name: str
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    uniques: list[UniqueConstraint] = field(default_factory=list)
    checks: list[CheckConstraint] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    estimated_rows: int | None = None

    @property
    def key(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def writable_columns(self) -> list[Column]:
        return [c for c in self.columns if c.writable]

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"{self.key} has no column {name!r}")


@dataclass
class Catalog:
    database: str
    server_version: str
    tables: list[Table] = field(default_factory=list)
    schemas: list[str] = field(default_factory=list)

    def table(self, key: str) -> Table:
        for table in self.tables:
            if table.key == key:
                return table
        raise KeyError(f"unknown table {key!r}")

    @property
    def table_keys(self) -> list[str]:
        return [t.key for t in self.tables]

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Catalog":
        tables = []
        for raw in data.get("tables", []):
            columns = [Column(**{**c, "data_kind": DataKind(c["data_kind"]) if c.get("data_kind") else None}) for c in raw.get("columns", [])]
            foreign_keys = [ForeignKey(**fk) for fk in raw.get("foreign_keys", [])]
            uniques = [UniqueConstraint(**u) for u in raw.get("uniques", [])]
            checks = [CheckConstraint(**c) for c in raw.get("checks", [])]
            triggers = [Trigger(**t) for t in raw.get("triggers", [])]
            tables.append(
                Table(
                    schema=raw["schema"],
                    name=raw["name"],
                    columns=columns,
                    primary_key=raw.get("primary_key", []),
                    foreign_keys=foreign_keys,
                    uniques=uniques,
                    checks=checks,
                    triggers=triggers,
                    estimated_rows=raw.get("estimated_rows"),
                )
            )
        return cls(
            database=data.get("database", ""),
            server_version=data.get("server_version", ""),
            tables=tables,
            schemas=data.get("schemas", []),
        )


def to_plain(obj: Any) -> Any:
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    return obj


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_qualified(schema: str, name: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(name)}"
