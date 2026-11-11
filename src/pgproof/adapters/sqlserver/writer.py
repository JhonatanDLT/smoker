"""Write generated rows into SQL Server with ``pyodbc``."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...core.errors import LoadError
from ...core.ir import Table
from .dialect import SqlServerDialect
from .errors import from_pyodbc
from .introspect import _cursor
from .serialization import text

BATCH_SIZE = 1000


class SqlServerRowWriter:
    def __init__(
        self,
        connection: Any,
        dialect: SqlServerDialect | None = None,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self.connection = connection
        self.dialect = dialect or SqlServerDialect()
        self.batch_size = max(1, batch_size)

    def truncate(self, tables: list[Table]) -> None:
        with _cursor(self.connection) as cursor:
            for statement in self.dialect.script_truncate(tables):
                cursor.execute(statement)

    def write(
        self,
        table: Table,
        rows: Iterable[tuple[Any, ...]],
        method: str = "batch",
        hasher: Any = None,
    ) -> int:
        columns = table.writable_columns
        if not columns:
            return 0
        qualified = self.dialect.quote_table(table)
        column_list = ", ".join(
            self.dialect.quote_ident(column.name) for column in columns
        )
        placeholders = ", ".join("?" for _ in columns)
        statement = (
            f"INSERT INTO {qualified} ({column_list}) VALUES ({placeholders})"
        )
        names = [column.name for column in columns]
        identity = any(column.identity for column in table.columns)
        count = 0
        with _cursor(self.connection) as cursor:
            cursor.fast_executemany = True
            if identity:
                cursor.execute(f"SET IDENTITY_INSERT {qualified} ON")
            try:
                batch: list[tuple[Any, ...]] = []
                for row in rows:
                    batch.append(tuple(row))
                    if hasher is not None:
                        line = "\t".join(text(value) for value in row) + "\n"
                        hasher.update(line.encode("utf-8"))
                    if len(batch) >= self.batch_size:
                        self._execute(
                            cursor, statement, batch, table.key, names, count
                        )
                        count += len(batch)
                        batch = []
                if batch:
                    self._execute(
                        cursor, statement, batch, table.key, names, count
                    )
                    count += len(batch)
            finally:
                if identity:
                    cursor.execute(f"SET IDENTITY_INSERT {qualified} OFF")
        return count

    def _execute(
        self,
        cursor: Any,
        statement: str,
        batch: list[tuple[Any, ...]],
        table: str,
        columns: list[str],
        start: int,
    ) -> None:
        try:
            cursor.executemany(statement, batch)
        except Exception as exc:
            raise from_pyodbc(
                exc,
                table,
                row_index=start,
                columns=columns,
                row=list(batch[0]) if batch else None,
            ) from exc

    def reset_sequences(self, tables: list[Table]) -> None:
        with _cursor(self.connection) as cursor:
            for statement in self.dialect.script_reset_sequences(tables):
                cursor.execute(statement)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


__all__ = ["LoadError", "SqlServerRowWriter"]
