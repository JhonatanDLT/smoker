"""BULK INSERT writer for on-prem SQL Server.

The file must be visible to the SQL Server service. Use ``server_dir`` for the
path inside the server and ``local_dir`` for the path the client writes to; in
the simplest case both are the same shared directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ...core.ir import Table
from .dialect import SqlServerDialect
from .errors import from_pyodbc
from .introspect import _cursor
from .serialization import text as canonical_text
from .writer import SqlServerRowWriter


def _field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str):
        return (
            value.replace("\\", "\\\\")
            .replace("\t", " ")
            .replace("\r", " ")
            .replace("\n", " ")
        )
    return canonical_text(value)


class SqlServerBulkWriter(SqlServerRowWriter):
    def __init__(
        self,
        connection: Any,
        dialect: SqlServerDialect | None = None,
        local_dir: str | Path | None = None,
        server_dir: str | Path | None = None,
    ) -> None:
        super().__init__(connection, dialect)
        if local_dir is None or server_dir is None:
            raise ValueError(
                "BULK INSERT needs --bulk-local-dir and --bulk-dir "
                "(a directory visible to SQL Server)"
            )
        if not self.dialect.capabilities.bulk_insert_from_file:
            raise ValueError(
                f"dialect {self.dialect.name!r} does not support BULK INSERT from files"
            )
        self.local_dir = Path(local_dir)
        self.server_dir = str(server_dir).rstrip("/\\")
        self.local_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        table: Table,
        rows: Iterable[tuple[Any, ...]],
        method: str = "bulk",
        hasher: Any = None,
    ) -> int:
        columns = table.writable_columns
        if not columns:
            return 0
        filename = (
            f"{table.database or 'db'}_{table.schema}_{table.name}.tsv"
        )
        filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
        local_path = self.local_dir / filename
        server_path = f"{self.server_dir}/{filename}"
        count = 0
        with local_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write("\t".join(_field(value) for value in row) + "\n")
                if hasher is not None:
                    line = "\t".join(canonical_text(value) for value in row) + "\n"
                    hasher.update(line.encode("utf-8"))
                count += 1

        qualified = self.dialect.quote_table(table)
        options = [
            "FIELDTERMINATOR = '\\t'",
            "ROWTERMINATOR = '\\n'",
            "TABLOCK",
            "CHECK_CONSTRAINTS",
        ]
        if table.triggers:
            options.append("FIRE_TRIGGERS")
        identity = any(column.identity for column in table.columns)
        if identity:
            options.append("KEEPIDENTITY")
        escaped_path = server_path.replace("'", "''")
        statement = (
            f"BULK INSERT {qualified} FROM '{escaped_path}' "
            f"WITH ({', '.join(options)})"
        )
        with _cursor(self.connection) as cursor:
            try:
                if identity:
                    cursor.execute(f"SET IDENTITY_INSERT {qualified} ON")
                cursor.execute(statement)
            except Exception as exc:
                raise from_pyodbc(
                    exc,
                    table.key,
                    row_index=0,
                    columns=[column.name for column in columns],
                ) from exc
            finally:
                if identity:
                    cursor.execute(f"SET IDENTITY_INSERT {qualified} OFF")
        return count


__all__ = ["SqlServerBulkWriter"]
