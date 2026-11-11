"""T-SQL implementation of the SQL dialect contract."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from ...core.dialect import Capabilities
from ...core.ir import (
    Column,
    DataKind,
    ExclusionConstraint,
    ForeignKey,
    Table,
)


class SqlServerDialect:
    name = "sqlserver"
    capabilities = Capabilities(
        exclusion_constraints=False,
        native_ranges=False,
        native_enums=False,
        arrays=False,
        partition_model="scheme",
        identity=True,
        identity_insert=True,
        computed_columns=True,
        rowversion=True,
        sequences=True,
        truncate_cascade=False,
        row_identity="pk",
        not_valid_constraints=False,
        filtered_indexes=True,
        temporal_tables=True,
        bulk_method="batch",
        sample_method="tablesample",
    )

    # ---------------------------------------------------------- primitives
    def quote_ident(self, name: str) -> str:
        return "[" + name.replace("]", "]]") + "]"

    def quote_qualified(self, schema: str, name: str) -> str:
        return f"{self.quote_ident(schema)}.{self.quote_ident(name)}"

    def quote_table_parts(
        self, database: str | None, schema: str, name: str
    ) -> str:
        if database:
            if not self.capabilities.cross_database:
                raise ValueError(
                    "cross-database references are not supported by this dialect"
                )
            return (
                f"{self.quote_ident(database)}."
                f"{self.quote_ident(schema)}.{self.quote_ident(name)}"
            )
        return f"{self.quote_ident(schema)}.{self.quote_ident(name)}"

    def quote_table(self, table: Table) -> str:
        return self.quote_table_parts(table.database, table.schema, table.name)

    def literal(self, value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, Decimal)):
            return str(value)
        if isinstance(value, datetime):
            return "'" + value.isoformat(sep=" ") + "'"
        if isinstance(value, (date, time)):
            return "'" + value.isoformat() + "'"
        if isinstance(value, (bytes, bytearray)):
            return "0x" + bytes(value).hex()
        return "N'" + str(value).replace("'", "''") + "'"

    def classify_type(
        self, sql_type: str, enum_values: list[str] | None = None
    ) -> DataKind:
        if enum_values:
            return DataKind.ENUM
        t = sql_type.lower().strip()
        if t.startswith(("nvarchar", "nchar")):
            return DataKind.TEXT
        if t in {"bit"}:
            return DataKind.BOOLEAN
        if t in {"tinyint", "smallint"}:
            return DataKind.SMALLINT
        if t in {"int", "integer"}:
            return DataKind.INT
        if t == "bigint":
            return DataKind.BIGINT
        if t.startswith(("decimal", "numeric")) or t in {"money", "smallmoney"}:
            return DataKind.NUMERIC
        if t in {"float", "real"} or t.startswith("float("):
            return DataKind.FLOAT
        if t in {
            "char", "varchar", "nchar", "nvarchar", "text", "ntext", "sysname"
        } or t.startswith(("char(", "varchar(", "nchar(", "nvarchar(")):
            return DataKind.TEXT
        if t == "uniqueidentifier":
            return DataKind.UUID
        if t == "date":
            return DataKind.DATE
        if t in {"datetime", "datetime2", "smalldatetime"} or t.startswith("datetime2("):
            return DataKind.TIMESTAMP
        if t == "datetimeoffset" or t.startswith("datetimeoffset("):
            return DataKind.TIMESTAMPTZ
        if t == "time" or t.startswith("time("):
            return DataKind.TIME
        if t in {"binary", "varbinary", "image"} or t.startswith(
            ("binary(", "varbinary(")
        ):
            return DataKind.BYTEA
        if t == "xml":
            return DataKind.JSON
        return DataKind.OTHER

    # ------------------------------------------------------ invariant SQL
    def not_null_sql(self, table: Table, column: Column) -> str:
        qualified = self.quote_table(table)
        return (
            f"SELECT 1 AS violation FROM {qualified} "
            f"WHERE {self.quote_ident(column.name)} IS NULL"
        )

    def enum_sql(self, table: Table, column: Column) -> str:
        qualified = self.quote_table(table)
        name = self.quote_ident(column.name)
        values = ", ".join(self.literal(value) for value in column.enum_values)
        return (
            f"SELECT 1 AS violation FROM {qualified} "
            f"WHERE {name} IS NOT NULL AND {name} NOT IN ({values})"
        )

    def unique_sql(
        self, table: Table, columns: list[str], predicate: str | None = None
    ) -> str:
        qualified = self.quote_table(table)
        column_list = ", ".join(self.quote_ident(name) for name in columns)
        not_null = " AND ".join(
            f"{self.quote_ident(name)} IS NOT NULL" for name in columns
        )
        where = f"{not_null} AND ({predicate})" if predicate else not_null
        return (
            f"SELECT {column_list}, COUNT_BIG(*) AS duplicates FROM {qualified} "
            f"WHERE {where} GROUP BY {column_list} HAVING COUNT_BIG(*) > 1"
        )

    def foreign_key_sql(self, table: Table, fk: ForeignKey) -> str:
        qualified = self.quote_table(table)
        child_columns = ", ".join(
            self.quote_ident(name) for name in fk.child_columns
        )
        conditions = " AND ".join(
            f"c.{self.quote_ident(name)} IS NOT NULL" for name in fk.child_columns
        )
        joins = " AND ".join(
            f"p.{self.quote_ident(parent)} = c.{self.quote_ident(child)}"
            for child, parent in zip(fk.child_columns, fk.parent_columns)
        )
        parent = self.quote_table_parts(
            fk.parent_database, fk.parent_schema, fk.parent_table
        )
        return (
            f"SELECT {child_columns} FROM {qualified} c "
            f"WHERE {conditions} AND NOT EXISTS ("
            f"SELECT 1 FROM {parent} p WHERE {joins})"
        )

    def check_sql(self, table: Table, predicate: str) -> str:
        qualified = self.quote_table(table)
        return (
            f"SELECT 1 AS violation FROM {qualified} "
            f"WHERE NOT ({predicate})"
        )

    def exclusion_sql(
        self, table: Table, exclusion: ExclusionConstraint
    ) -> str | None:
        return None

    def _history_table(self, table: Table) -> str | None:
        info = table.temporal
        if info is None or not info.history_table:
            return None
        parts = info.history_table.split(".")
        if len(parts) >= 3:
            database, schema, name = parts[-3], parts[-2], parts[-1]
        elif len(parts) == 2:
            database, schema, name = None, parts[0], parts[1]
        else:
            database, schema, name = table.database, table.schema, parts[0]
        return self.quote_table_parts(database, schema, name)

    def _period_columns(self, table: Table) -> tuple[str, str] | None:
        info = table.temporal
        if info is None or not info.start_column or not info.end_column:
            return None
        return (
            self.quote_ident(info.start_column),
            self.quote_ident(info.end_column),
        )

    def temporal_history_sql(self, table: Table) -> str | None:
        history = self._history_table(table)
        period = self._period_columns(table)
        if history is None or period is None:
            return None
        start, end = period
        keys = ", ".join(self.quote_ident(name) for name in table.primary_key)
        partition = keys or "1"
        select_keys = f"{keys}, " if keys else ""
        return (
            "SELECT 1 AS violation FROM ("
            f"SELECT {select_keys}{start} AS period_start, {end} AS period_end, "
            f"LAG({end}) OVER (PARTITION BY {partition} ORDER BY {start}) "
            "AS previous_end "
            f"FROM {history}) AS temporal_history "
            "WHERE period_start > period_end "
            "OR previous_end > period_start"
        )

    def temporal_current_sql(self, table: Table) -> str | None:
        history = self._history_table(table)
        period = self._period_columns(table)
        if history is None or period is None:
            return None
        current = self.quote_table(table)
        start, end = period
        if table.primary_key:
            join = " AND ".join(
                f"h.{self.quote_ident(name)} = c.{self.quote_ident(name)}"
                for name in table.primary_key
            )
        else:
            join = "1 = 1"
        return (
            f"SELECT 1 AS violation FROM {current} c "
            f"WHERE EXISTS (SELECT 1 FROM {history} h WHERE {join} "
            f"AND c.{start} < h.{end} AND h.{start} < c.{end})"
        )

    # ------------------------------------------------------ query wrapping
    def count_sql(self, sql: str) -> str:
        return f"SELECT COUNT_BIG(*) FROM ({sql}) AS violations"

    def sample_sql(self, sql: str, limit: int) -> str:
        return f"SELECT TOP ({int(limit)}) * FROM ({sql}) AS violations"

    # ------------------------------------------------------ SQL scripts
    def script_begin(self) -> list[str]:
        return [
            "SET ANSI_NULLS ON;",
            "SET QUOTED_IDENTIFIER ON;",
            "SET XACT_ABORT ON;",
            "BEGIN TRANSACTION;",
        ]

    def script_commit(self) -> list[str]:
        return ["COMMIT TRANSACTION;"]

    def script_rollback(self) -> list[str]:
        return ["IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;"]

    def script_truncate(self, tables: list[Table]) -> list[str]:
        if not tables:
            return []
        by_key = {table.key: table for table in tables}
        has_edges = any(
            fk.parent_key in by_key and fk.parent_key != table.key
            for table in tables
            for fk in table.foreign_keys
        )
        has_temporal = any(table.temporal is not None for table in tables)
        if not has_edges and not has_temporal:
            return [
                f"TRUNCATE TABLE {self.quote_table(table)};"
                for table in tables
            ]
        return [
            f"DELETE FROM {self.quote_table(table)};"
            for table in self._delete_order(tables)
        ]

    def _delete_order(self, tables: list[Table]) -> list[Table]:
        by_key = {table.key: table for table in tables}
        dependents: dict[str, set[str]] = {key: set() for key in by_key}
        for table in tables:
            for fk in table.foreign_keys:
                if fk.parent_key in by_key and fk.parent_key != table.key:
                    dependents[fk.parent_key].add(table.key)
        remaining = set(by_key)
        ordered: list[Table] = []
        while remaining:
            ready = sorted(
                key for key in remaining if not (dependents[key] & remaining)
            )
            if not ready:
                ready = [min(remaining)]
            for key in ready:
                remaining.remove(key)
                ordered.append(by_key[key])
        return ordered

    def script_reset_sequences(self, tables: list[Table]) -> list[str]:
        statements: list[str] = []
        counter = 0
        for table in tables:
            qualified = self.quote_table(table)
            relation = qualified.replace("'", "''")
            for column in table.columns:
                if not column.identity:
                    continue
                variable = f"@smoker_reseed_{counter}"
                counter += 1
                name = self.quote_ident(column.name)
                statements.append(
                    f"DECLARE {variable} BIGINT; "
                    f"SELECT {variable} = ISNULL(MAX({name}), 0) FROM {qualified}; "
                    f"DBCC CHECKIDENT (N'{relation}', RESEED, {variable});"
                )
        return statements

    def script_before_insert(self, table: Table) -> list[str]:
        if not any(column.identity for column in table.columns):
            return []
        qualified = self.quote_table(table)
        return [f"SET IDENTITY_INSERT {qualified} ON;"]

    def script_after_insert(self, table: Table) -> list[str]:
        if not any(column.identity for column in table.columns):
            return []
        qualified = self.quote_table(table)
        return [f"SET IDENTITY_INSERT {qualified} OFF;"]


def strip_check_definition(definition: str) -> str:
    """Turn a SQL Server ``([price]>(0))`` definition into a predicate."""
    text = definition.strip()
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


def type_text(
    type_name: str, max_length: int | None, precision: int | None, scale: int | None
) -> str:
    """Render a SQL Server type with its length/precision for the canonical IR."""
    t = type_name.lower()
    if t in {"nvarchar", "nchar"}:
        if max_length == -1:
            return f"{t}(max)"
        if max_length:
            return f"{t}({max_length // 2})"
    if t in {"varchar", "char", "varbinary", "binary"}:
        if max_length == -1:
            return f"{t}(max)"
        if max_length:
            return f"{t}({max_length})"
    if t in {"decimal", "numeric"} and precision is not None:
        return f"{t}({precision},{scale or 0})"
    if t in {"datetime2", "time", "datetimeoffset"} and scale is not None:
        return f"{t}({scale})"
    if t == "float" and precision is not None:
        return f"{t}({precision})"
    return t


__all__ = [
    "SqlServerDialect",
    "strip_check_definition",
    "type_text",
]
