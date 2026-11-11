"""SQL Server implementation of the QueryRunner port."""

from __future__ import annotations

import hashlib
from typing import Any

from ...core.ir import Table
from .dialect import SqlServerDialect
from .introspect import _cursor
from .serialization import text


def _jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return text(value)


class SqlServerQueryRunner:
    def __init__(
        self, connection: Any, dialect: SqlServerDialect | None = None
    ) -> None:
        self.connection = connection
        self.dialect = dialect or SqlServerDialect()

    def count(self, sql: str) -> int:
        with _cursor(self.connection) as cursor:
            cursor.execute(self.dialect.count_sql(sql))
            return int(cursor.fetchone()[0])

    def sample(self, sql: str, limit: int) -> list[dict[str, Any]]:
        with _cursor(self.connection) as cursor:
            cursor.execute(self.dialect.sample_sql(sql, limit))
            names = [column[0] for column in cursor.description]
            return [
                {name: _jsonable(value) for name, value in zip(names, row)}
                for row in cursor.fetchall()
            ]

    def identity(self) -> tuple[str, str]:
        with _cursor(self.connection) as cursor:
            cursor.execute(
                "SELECT DB_NAME(), "
                "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128))"
            )
            row = cursor.fetchone()
        return (str(row[0]) if row else "", str(row[1]) if row and row[1] else "")

    def unvalidated_constraints(self) -> list[str]:
        warnings: list[str] = []
        query = """
            SELECT s.name + '.' + t.name + ':' + cc.name
            FROM sys.check_constraints cc
            JOIN sys.tables t ON t.object_id = cc.parent_object_id
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            WHERE cc.is_disabled = 1 OR cc.is_not_trusted = 1
            UNION ALL
            SELECT s.name + '.' + t.name + ':' + fk.name
            FROM sys.foreign_keys fk
            JOIN sys.tables t ON t.object_id = fk.parent_object_id
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            WHERE fk.is_disabled = 1 OR fk.is_not_trusted = 1
            ORDER BY 1
        """
        with _cursor(self.connection) as cursor:
            cursor.execute(query)
            for (name,) in cursor.fetchall():
                warnings.append(
                    f"constraint {name} is disabled or not trusted on existing rows"
                )
        return warnings

    def table_counts(self, keys: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        with _cursor(self.connection) as cursor:
            for key in keys:
                schema, name = key.split(".", 1)
                qualified = self.dialect.quote_qualified(schema, name)
                cursor.execute(f"SELECT COUNT_BIG(*) FROM {qualified}")
                counts[key] = int(cursor.fetchone()[0])
        return counts

    def dataset_digest(self, tables: list[Table]) -> str:
        hasher = hashlib.sha256()
        with _cursor(self.connection) as cursor:
            for table in tables:
                columns = [column.name for column in table.writable_columns]
                if not columns:
                    continue
                column_list = ", ".join(
                    self.dialect.quote_ident(name) for name in columns
                )
                order_columns = table.primary_key or columns
                order_list = ", ".join(
                    self.dialect.quote_ident(name) for name in order_columns
                )
                qualified = self.dialect.quote_qualified(table.schema, table.name)
                cursor.execute(
                    f"SELECT {column_list} FROM {qualified} ORDER BY {order_list}"
                )
                for row in cursor:
                    hasher.update(
                        ("\t".join(text(value) for value in row) + "\n").encode(
                            "utf-8"
                        )
                    )
        return hasher.hexdigest()


__all__ = ["SqlServerQueryRunner"]
