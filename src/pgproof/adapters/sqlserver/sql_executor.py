"""SQL Server implementation of the SqlExecutor port."""

from __future__ import annotations

from typing import Any

from .introspect import _cursor


class SqlServerSqlExecutor:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def execute(self, sql: str) -> int:
        with _cursor(self.connection) as cursor:
            cursor.execute(sql)
            return int(cursor.rowcount) if cursor.rowcount >= 0 else 0

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()


__all__ = ["SqlServerSqlExecutor"]
