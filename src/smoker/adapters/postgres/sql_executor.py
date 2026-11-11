"""PostgreSQL implementation of the SqlExecutor port."""

from __future__ import annotations

import psycopg


class PostgresSqlExecutor:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str) -> int:
        with self.connection.cursor() as cur:
            cur.execute(sql)
            return max(cur.rowcount, 0)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()
