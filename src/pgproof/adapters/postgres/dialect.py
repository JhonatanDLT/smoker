"""PostgreSQL implementation of the SQL dialect contract."""

from __future__ import annotations

import re
from typing import Any

from ...core.dialect import Capabilities
from ...core.ir import (
    Column,
    DataKind,
    ExclusionConstraint,
    ForeignKey,
    Table,
    classify_type,
    quote_ident,
    quote_qualified,
)
from .serialization import sql_literal

TRUNCATE_BATCH_SIZE = 100


class PostgresDialect:
    name = "postgres"
    capabilities = Capabilities(
        exclusion_constraints=True,
        native_ranges=True,
        native_enums=True,
        arrays=True,
        partition_model="declarative",
        identity=True,
        identity_insert=True,
        computed_columns=True,
        rowversion=False,
        sequences=True,
        truncate_cascade=True,
        row_identity="ctid",
        not_valid_constraints=True,
        filtered_indexes=True,
        temporal_tables=False,
        bulk_method="copy",
        sample_method="tablesample",
    )

    # ---------------------------------------------------------- primitives
    def quote_ident(self, name: str) -> str:
        return quote_ident(name)

    def quote_qualified(self, schema: str, name: str) -> str:
        return quote_qualified(schema, name)

    def literal(self, value: Any) -> str:
        return sql_literal(value)

    def classify_type(
        self, sql_type: str, enum_values: list[str] | None = None
    ) -> DataKind:
        return classify_type(sql_type, enum_values)

    # ------------------------------------------------------ invariant SQL
    def not_null_sql(self, table: Table, column: Column) -> str:
        qualified = self.quote_qualified(table.schema, table.name)
        return (
            f"SELECT 1 AS violation FROM {qualified} "
            f"WHERE {self.quote_ident(column.name)} IS NULL"
        )

    def enum_sql(self, table: Table, column: Column) -> str:
        qualified = self.quote_qualified(table.schema, table.name)
        name = self.quote_ident(column.name)
        values = ", ".join(self.literal(value) for value in column.enum_values)
        return (
            f"SELECT 1 AS violation FROM {qualified} "
            f"WHERE {name} IS NOT NULL AND {name}::text NOT IN ({values})"
        )

    def unique_sql(self, table: Table, columns: list[str]) -> str:
        qualified = self.quote_qualified(table.schema, table.name)
        column_list = ", ".join(self.quote_ident(name) for name in columns)
        not_null = " AND ".join(
            f"{self.quote_ident(name)} IS NOT NULL" for name in columns
        )
        return (
            f"SELECT {column_list}, count(*) AS duplicates FROM {qualified} "
            f"WHERE {not_null} GROUP BY {column_list} HAVING count(*) > 1"
        )

    def foreign_key_sql(self, table: Table, fk: ForeignKey) -> str:
        qualified = self.quote_qualified(table.schema, table.name)
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
        parent = self.quote_qualified(fk.parent_schema, fk.parent_table)
        return (
            f"SELECT {child_columns} FROM {qualified} c "
            f"WHERE {conditions} AND NOT EXISTS ("
            f"SELECT 1 FROM {parent} p WHERE {joins})"
        )

    def check_sql(self, table: Table, predicate: str) -> str:
        qualified = self.quote_qualified(table.schema, table.name)
        return (
            f"SELECT 1 AS violation FROM {qualified} "
            f"WHERE ({predicate}) IS FALSE"
        )

    def exclusion_sql(
        self, table: Table, exclusion: ExclusionConstraint
    ) -> str | None:
        if not exclusion.elements:
            return None
        qualified = self.quote_qualified(table.schema, table.name)
        conditions = " AND ".join(
            f"a.{self.quote_ident(element.column)} {element.operator} "
            f"b.{self.quote_ident(element.column)}"
            for element in exclusion.elements
        )
        predicate = (
            f" AND {self._qualify_predicate(exclusion.predicate, table)}"
            if exclusion.predicate
            else ""
        )
        return (
            f"SELECT a.ctid FROM {qualified} a JOIN {qualified} b "
            f"ON a.ctid < b.ctid WHERE {conditions}{predicate}"
        )

    def _qualify_predicate(self, predicate: str, table: Table) -> str:
        columns = {column.name for column in table.columns}
        result: list[str] = []
        index = 0
        in_string = False
        while index < len(predicate):
            char = predicate[index]
            if char == "'":
                if (
                    in_string
                    and index + 1 < len(predicate)
                    and predicate[index + 1] == "'"
                ):
                    result.append("''")
                    index += 2
                    continue
                in_string = not in_string
                result.append(char)
                index += 1
                continue
            if in_string:
                result.append(char)
                index += 1
                continue
            match = re.match(r"[A-Za-z_][A-Za-z0-9_]*", predicate[index:])
            if match is not None:
                word = match.group(0)
                before = predicate[index - 1] if index > 0 else ""
                if word in columns and before != ".":
                    result.append(f"a.{self.quote_ident(word)}")
                else:
                    result.append(word)
                index += len(word)
                continue
            result.append(char)
            index += 1
        return "".join(result)

    # ------------------------------------------------------ query wrapping
    def count_sql(self, sql: str) -> str:
        return f"SELECT count(*) FROM ({sql}) AS violations"

    def sample_sql(self, sql: str, limit: int) -> str:
        return f"SELECT * FROM ({sql}) AS violations LIMIT {int(limit)}"

    # ------------------------------------------------------ SQL scripts
    def script_begin(self) -> list[str]:
        return ["BEGIN;"]

    def script_commit(self) -> list[str]:
        return ["COMMIT;"]

    def script_rollback(self) -> list[str]:
        return ["ROLLBACK;"]

    def script_truncate(self, tables: list[Table]) -> list[str]:
        statements: list[str] = []
        for start in range(0, len(tables), TRUNCATE_BATCH_SIZE):
            batch = tables[start : start + TRUNCATE_BATCH_SIZE]
            targets = ", ".join(
                self.quote_qualified(table.schema, table.name) for table in batch
            )
            statements.append(f"TRUNCATE {targets} CASCADE;")
        return statements

    def script_reset_sequences(self, tables: list[Table]) -> list[str]:
        statements: list[str] = []
        for table in tables:
            qualified = self.quote_qualified(table.schema, table.name)
            for column in table.columns:
                if not (
                    column.identity
                    or (column.default or "").startswith("nextval")
                ):
                    continue
                relation = qualified.replace("'", "''")
                name = self.quote_ident(column.name)
                statements.append(
                    "SELECT setval("
                    f"pg_get_serial_sequence('{relation}', '{column.name}'), "
                    f"COALESCE((SELECT max({name}) FROM {qualified}), 1), "
                    f"(SELECT count(*) > 0 FROM {qualified})"
                    ");"
                )
        return statements

    def script_before_insert(self, table: Table) -> list[str]:
        return []

    def script_after_insert(self, table: Table) -> list[str]:
        return []
