"""Writing generated rows into PostgreSQL.

Errors are translated into ``LoadError`` with the table, constraint, row index,
row values and SQLSTATE. The application layer may enrich the row by replaying
the deterministic generator when PostgreSQL only gives a COPY line number.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import psycopg

from ...core.errors import LoadError
from ...core.ir import Table, quote_ident, quote_qualified
from .errors import from_psycopg


def truncate(conn: psycopg.Connection, tables: list[Table], cascade: bool = True) -> None:
    if not tables:
        return
    targets = ", ".join(quote_qualified(table.schema, table.name) for table in tables)
    suffix = " CASCADE" if cascade else ""
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {targets}{suffix}")


def write_table(
    conn: psycopg.Connection,
    table: Table,
    rows: Iterable[tuple[Any, ...]],
    method: str = "auto",
    hasher: Any = None,
) -> int:
    columns = table.writable_columns
    if not columns:
        return 0
    chosen = method
    if method == "auto":
        chosen = "copy"
    if chosen == "insert" and any(fk.parent_key == table.key for fk in table.foreign_keys):
        # Self-references need the whole table present before PostgreSQL checks
        # the constraint, which only happens at the end of a single COPY.
        chosen = "copy"

    if chosen == "copy":
        return _copy_rows(conn, table, columns, rows, hasher=hasher)
    return _insert_rows(conn, table, columns, rows, hasher=hasher)


def _copy_rows(
    conn: psycopg.Connection,
    table: Table,
    columns: list,
    rows: Iterable[tuple[Any, ...]],
    hasher: Any = None,
) -> int:
    qualified = quote_qualified(table.schema, table.name)
    column_list = ", ".join(quote_ident(column.name) for column in columns)
    statement = f"COPY {qualified} ({column_list}) FROM STDIN"
    count = 0
    try:
        with conn.cursor() as cur, cur.copy(statement) as copy:
            for row in rows:
                line = "\t".join(_copy_text(value) for value in row) + "\n"
                copy.write(line)
                if hasher is not None:
                    hasher.update(line.encode("utf-8"))
                count += 1
    except psycopg.Error as exc:
        raise from_psycopg(exc, table.key) from exc
    return count


def _insert_rows(
    conn: psycopg.Connection,
    table: Table,
    columns: list,
    rows: Iterable[tuple[Any, ...]],
    hasher: Any = None,
) -> int:
    qualified = quote_qualified(table.schema, table.name)
    column_list = ", ".join(quote_ident(column.name) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    statement = f"INSERT INTO {qualified} ({column_list}) VALUES ({placeholders})"
    names = [column.name for column in columns]
    count = 0
    with conn.cursor() as cur:
        for row in rows:
            try:
                cur.execute(statement, row)
            except psycopg.Error as exc:
                raise from_psycopg(
                    exc,
                    table.key,
                    row_index=count,
                    columns=names,
                    row=list(row),
                ) from exc
            if hasher is not None:
                line = "\t".join(_copy_text(value) for value in row) + "\n"
                hasher.update(line.encode("utf-8"))
            count += 1
    return count


def _copy_text(value: Any) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, bytearray)):
        return "\\x" + bytes(value).hex()
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def reset_sequences(conn: psycopg.Connection, tables: list[Table]) -> None:
    with conn.cursor() as cur:
        for table in tables:
            for column in table.columns:
                if not (column.identity or (column.default or "").startswith("nextval")):
                    continue
                cur.execute(
                    "SELECT pg_get_serial_sequence(%s, %s)",
                    (f"{table.schema}.{table.name}", column.name),
                )
                row = cur.fetchone()
                sequence = row[0] if row else None
                if not sequence:
                    continue
                qualified = quote_qualified(table.schema, table.name)
                cur.execute(
                    f"""
                    SELECT setval(
                        %s,
                        COALESCE((SELECT max({quote_ident(column.name)}) FROM {qualified}), 1),
                        (SELECT count(*) > 0 FROM {qualified})
                    )
                    """,
                    (sequence,),
                )


def iter_batches(rows: Iterator[tuple[Any, ...]], size: int = 5000) -> Iterator[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class PostgresRowWriter:
    """Adapter implementing the RowWriter port."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def truncate(self, tables: list[Table]) -> None:
        truncate(self.connection, tables)

    def write(
        self,
        table: Table,
        rows: Iterable[tuple[Any, ...]],
        method: str = "copy",
        hasher: Any = None,
    ) -> int:
        return write_table(self.connection, table, rows, method=method, hasher=hasher)

    def reset_sequences(self, tables: list[Table]) -> None:
        reset_sequences(self.connection, tables)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


__all__ = [
    "LoadError",
    "PostgresRowWriter",
    "iter_batches",
    "reset_sequences",
    "truncate",
    "write_table",
]
