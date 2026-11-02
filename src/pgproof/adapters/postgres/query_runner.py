"""PostgreSQL implementation of the QueryRunner port."""

from __future__ import annotations

from datetime import date, datetime
from datetime import time as time_value
from decimal import Decimal
from typing import Any

import psycopg

from ...core.ir import quote_qualified


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime, time_value)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class PostgresQueryRunner:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def count(self, sql: str) -> int:
        with self.connection.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM ({sql}) AS violations")
            return int(cur.fetchone()[0])

    def sample(self, sql: str, limit: int) -> list[dict[str, Any]]:
        with self.connection.cursor() as cur:
            cur.execute(f"SELECT * FROM ({sql}) AS violations LIMIT %s", (limit,))
            names = [description.name for description in cur.description]
            return [
                {name: _jsonable(value) for name, value in zip(names, row)}
                for row in cur.fetchall()
            ]

    def identity(self) -> tuple[str, str]:
        with self.connection.cursor() as cur:
            cur.execute("SELECT current_database(), current_setting('server_version')")
            database, version = cur.fetchone()
        return database, version

    def unvalidated_constraints(self) -> list[str]:
        warnings: list[str] = []
        with self.connection.cursor() as cur:
            cur.execute(
                """
                SELECT n.nspname || '.' || c.relname || ':' || con.conname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE NOT con.convalidated
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                ORDER BY 1
                """
            )
            for (name,) in cur.fetchall():
                warnings.append(
                    f"constraint {name} is NOT VALID and has not been checked on existing rows"
                )
        return warnings

    def table_counts(self, keys: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.connection.cursor() as cur:
            for key in keys:
                schema, name = key.split(".", 1)
                cur.execute(f"SELECT count(*) FROM {quote_qualified(schema, name)}")
                counts[key] = int(cur.fetchone()[0])
        return counts
