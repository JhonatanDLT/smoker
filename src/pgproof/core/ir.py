"""Core data model for pgproof.

Everything in this package is pure: no database drivers, no I/O, no CLI.
Adapters translate a dialect into this model; the engine never looks back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
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
    RANGE = "range"
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
    if t in {"int4range", "int8range", "numrange", "daterange", "tsrange", "tstzrange"} or t.endswith("range"):
        return DataKind.RANGE
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
    inferred: bool = False
    provenance: Provenance | None = None
    child_database: str | None = None
    parent_database: str | None = None

    @property
    def parent_key(self) -> str:
        if self.parent_database:
            return f"{self.parent_database}.{self.parent_schema}.{self.parent_table}"
        return f"{self.parent_schema}.{self.parent_table}"

    @property
    def child_key(self) -> str:
        if self.child_database:
            return f"{self.child_database}.{self.child_schema}.{self.child_table}"
        return f"{self.child_schema}.{self.child_table}"


@dataclass
class Provenance:
    kind: str  # dump | function | trigger | view | inferred | catalog
    source: str
    detail: str | None = None


@dataclass
class RuleCandidate:
    id: str
    kind: str  # guard | derived | filter (segmentation only)
    table: str
    expression: str
    columns: list[str] = field(default_factory=list)
    confidence: float = 0.0
    applied: bool = False  # segmentation rules never bound the generation space
    provenance: Provenance | None = None
    note: str | None = None
    scope: str = "row"  # row | statement | aggregate
    category: str = "segmentation"  # table constraints are not rules


@dataclass
class SqlObject:
    kind: str
    schema: str | None
    name: str | None
    definition: str
    provenance: Provenance
    depends_on: list[str] = field(default_factory=list)
    database: str | None = None


@dataclass
class ExclusionElement:
    column: str
    operator: str


@dataclass
class ExclusionConstraint:
    name: str
    elements: list[ExclusionElement] = field(default_factory=list)
    method: str | None = None
    predicate: str | None = None
    definition: str = ""
    provenance: Provenance | None = None


@dataclass
class UniqueConstraint:
    name: str
    columns: list[str]
    predicate: str | None = None
    provenance: Provenance | None = None


@dataclass
class CheckConstraint:
    name: str
    definition: str
    predicate: str | None = None
    validated: bool = True
    provenance: Provenance | None = None


@dataclass
class Trigger:
    name: str
    timing: str
    events: str
    function: str
    definition: str
    body: str | None = None
    function_oid: int | None = None
    when_clause: str | None = None
    update_of: list[str] = field(default_factory=list)
    row_level: bool = True
    provenance: Provenance | None = None


@dataclass
class Function:
    oid: int
    schema: str
    name: str
    body: str
    kind: str = "function"
    language: str | None = None
    returns: str | None = None
    parameters: list[str] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    provenance: Provenance | None = None
    database: str | None = None

    @property
    def key(self) -> str:
        if self.database:
            return f"{self.database}.{self.schema}.{self.name}"
        return f"{self.schema}.{self.name}"


@dataclass
class Partition:
    schema: str
    name: str
    strategy: str  # range | list | hash | default
    columns: list[str] = field(default_factory=list)
    lower: list[Any] | None = None
    upper: list[Any] | None = None
    values: list[list[Any]] = field(default_factory=list)
    modulus: int | None = None
    remainder: int | None = None
    estimated_rows: int = 0
    children: list[Partition] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Partition:
        lower = data.get("lower")
        upper = data.get("upper")
        values = data.get("values") or []
        if lower is not None and not isinstance(lower, list):
            lower = [lower]
        if upper is not None and not isinstance(upper, list):
            upper = [upper]
        if values and not isinstance(values[0], list):
            values = [[value] for value in values]
        return cls(
            schema=data.get("schema", ""),
            name=data.get("name", ""),
            strategy=data.get("strategy", "default"),
            columns=list(data.get("columns", [])),
            lower=lower,
            upper=upper,
            values=values,
            modulus=data.get("modulus"),
            remainder=data.get("remainder"),
            estimated_rows=int(data.get("estimated_rows", 0)),
            children=[cls.from_dict(child) for child in data.get("children", [])],
        )


@dataclass
class TemporalInfo:
    history_table: str | None = None
    start_column: str | None = None
    end_column: str | None = None


@dataclass
class Table:
    schema: str
    name: str
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    uniques: list[UniqueConstraint] = field(default_factory=list)
    checks: list[CheckConstraint] = field(default_factory=list)
    exclusions: list[ExclusionConstraint] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    estimated_rows: int | None = None
    is_partitioned: bool = False
    is_partition: bool = False
    has_sub_partitions: bool = False
    partition_strategy: str | None = None
    partition_key: list[str] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)
    provenance: Provenance | None = None
    database: str | None = None
    temporal: TemporalInfo | None = None

    @property
    def key(self) -> str:
        if self.database:
            return f"{self.database}.{self.schema}.{self.name}"
        return f"{self.schema}.{self.name}"

    @property
    def writable_columns(self) -> list[Column]:
        return [c for c in self.columns if c.writable]

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"{self.key} has no column {name!r}")


def _provenance(raw: dict[str, Any] | None) -> Provenance | None:
    if not raw:
        return None
    return Provenance(
        kind=raw.get("kind", "dump"),
        source=raw.get("source", ""),
        detail=raw.get("detail"),
    )


@dataclass
class Catalog:
    database: str
    server_version: str
    tables: list[Table] = field(default_factory=list)
    schemas: list[str] = field(default_factory=list)
    functions: list[Function] = field(default_factory=list)
    objects: list[SqlObject] = field(default_factory=list)
    rules: list[RuleCandidate] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)

    def table(self, key: str) -> Table:
        for table in self.tables:
            if table.key == key:
                return table
        raise KeyError(f"unknown table {key!r}")

    def function(self, oid: int | None) -> Function | None:
        if oid is None:
            return None
        for function in self.functions:
            if function.oid == oid:
                return function
        return None

    @property
    def table_keys(self) -> list[str]:
        return [t.key for t in self.tables]

    def to_dict(self) -> dict[str, Any]:
        return to_plain(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Catalog:
        tables = []
        for raw in data.get("tables", []):
            columns = [Column(**{**c, "data_kind": DataKind(c["data_kind"]) if c.get("data_kind") else None}) for c in raw.get("columns", [])]
            foreign_keys = [
                ForeignKey(
                    **{
                        **fk,
                        "provenance": _provenance(fk.get("provenance")),
                    }
                )
                for fk in raw.get("foreign_keys", [])
            ]
            uniques = [
                UniqueConstraint(
                    **{
                        **unique,
                        "provenance": _provenance(unique.get("provenance")),
                    }
                )
                for unique in raw.get("uniques", [])
            ]
            checks = [
                CheckConstraint(
                    **{
                        **check,
                        "provenance": _provenance(check.get("provenance")),
                    }
                )
                for check in raw.get("checks", [])
            ]
            exclusions = [
                ExclusionConstraint(
                    **{
                        **exclusion,
                        "elements": [
                            ExclusionElement(**element)
                            for element in exclusion.get("elements", [])
                        ],
                        "provenance": _provenance(exclusion.get("provenance")),
                    }
                )
                for exclusion in raw.get("exclusions", [])
            ]
            triggers = [
                Trigger(
                    **{
                        **trigger,
                        "provenance": _provenance(trigger.get("provenance")),
                    }
                )
                for trigger in raw.get("triggers", [])
            ]
            partitions = [Partition.from_dict(p) for p in raw.get("partitions", [])]
            tables.append(
                Table(
                    schema=raw["schema"],
                    name=raw["name"],
                    columns=columns,
                    primary_key=raw.get("primary_key", []),
                    foreign_keys=foreign_keys,
                    uniques=uniques,
                    checks=checks,
                    exclusions=exclusions,
                    triggers=triggers,
                    estimated_rows=raw.get("estimated_rows"),
                    is_partitioned=raw.get("is_partitioned", False),
                    is_partition=raw.get("is_partition", False),
                    has_sub_partitions=raw.get("has_sub_partitions", False),
                    partition_strategy=raw.get("partition_strategy"),
                    partition_key=raw.get("partition_key", []),
                    partitions=partitions,
                    provenance=_provenance(raw.get("provenance")),
                    database=raw.get("database"),
                    temporal=(
                        TemporalInfo(
                            history_table=raw["temporal"].get("history_table"),
                            start_column=raw["temporal"].get("start_column"),
                            end_column=raw["temporal"].get("end_column"),
                        )
                        if raw.get("temporal")
                        else None
                    ),
                )
            )
            _restore_partition_values(tables[-1])
        return cls(
            database=data.get("database", ""),
            server_version=data.get("server_version", ""),
            tables=tables,
            schemas=data.get("schemas", []),
            functions=[
                Function(
                    **{
                        **function,
                        "provenance": _provenance(function.get("provenance")),
                    }
                )
                for function in data.get("functions", [])
            ],
            objects=[
                SqlObject(
                    kind=obj.get("kind", "object"),
                    schema=obj.get("schema"),
                    name=obj.get("name"),
                    definition=obj.get("definition", ""),
                    provenance=_provenance(obj.get("provenance")) or Provenance("dump", ""),
                    depends_on=list(obj.get("depends_on", [])),
                    database=obj.get("database"),
                )
                for obj in data.get("objects", [])
            ],
            databases=list(data.get("databases", [])),
            rules=[
                RuleCandidate(
                    id=rule.get("id", ""),
                    kind=rule.get("kind", "check"),
                    table=rule.get("table", ""),
                    expression=rule.get("expression", ""),
                    columns=list(rule.get("columns", [])),
                    confidence=float(rule.get("confidence", 0.0)),
                    applied=bool(rule.get("applied", False)),
                    provenance=_provenance(rule.get("provenance")),
                    note=rule.get("note"),
                    scope=rule.get("scope", "row"),
                    category=rule.get("category", "segmentation"),
                )
                for rule in data.get("rules", [])
            ],
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
    if isinstance(obj, (date, datetime, time)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return None
    return obj


def _restore_scalar(value: Any, kind: DataKind) -> Any:
    if value is None:
        return None
    try:
        if kind == DataKind.DATE:
            return date.fromisoformat(str(value).split(" ")[0])
        if kind in (DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
            parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
            if kind == DataKind.TIMESTAMPTZ and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            return int(value)
        if kind == DataKind.NUMERIC:
            return Decimal(str(value))
        if kind == DataKind.FLOAT:
            return float(value)
        if kind == DataKind.BOOLEAN:
            return str(value).lower() in {"true", "t", "1"}
    except (ValueError, ArithmeticError):
        return value
    return value


def _restore_partition_values(table: Table) -> None:
    def restore(nodes: list[Partition]) -> None:
        for partition in nodes:
            columns: list[Column | None] = []
            for name in partition.columns:
                try:
                    columns.append(table.column(name))
                except KeyError:
                    columns.append(None)

            def value_at(value: Any, index: int, bound: list[Column | None] = columns) -> Any:
                column = bound[index] if index < len(bound) else None
                return _restore_scalar(
                    value, column.data_kind if column else DataKind.OTHER
                )

            if partition.strategy == "range":
                if partition.lower is not None:
                    partition.lower = [
                        value_at(value, index)
                        for index, value in enumerate(partition.lower)
                    ]
                if partition.upper is not None:
                    partition.upper = [
                        value_at(value, index)
                        for index, value in enumerate(partition.upper)
                    ]
            elif partition.strategy == "list":
                partition.values = [
                    [value_at(value, index) for index, value in enumerate(row)]
                    for row in partition.values
                ]
            restore(partition.children)

    restore(table.partitions)


def all_partition_columns(table: Table) -> list[str]:
    columns: list[str] = []
    if table.partition_key:
        columns.extend(table.partition_key)

    def walk(nodes: list[Partition]) -> None:
        for node in nodes:
            for name in node.columns:
                if name not in columns:
                    columns.append(name)
            walk(node.children)

    walk(table.partitions)
    return columns


def split_table_key(key: str) -> tuple[str | None, str, str]:
    """Split ``database.schema.table``, ``schema.table`` or ``table``."""
    parts = key.split(".")
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    return None, "public", parts[0]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def quote_qualified(schema: str, name: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(name)}"
