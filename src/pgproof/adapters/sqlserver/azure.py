"""Azure SQL Database dialect and connection helper.

Azure SQL is SQL Server as a managed service. The engine is compatible, but
cross-database references, `USE`, local-file `BULK INSERT` and some DMVs are
not available. Transient connections (serverless auto-pause, failovers) are
retried.
"""

from __future__ import annotations

import time
from dataclasses import replace

from .dialect import SqlServerDialect
from .introspect import SqlServerConnection

TRANSIENT_SQLSTATES = {
    "08001",
    "08S01",
    "40197",
    "40501",
    "40613",
    "49918",
    "49919",
    "49920",
}


class AzureSqlDialect(SqlServerDialect):
    name = "azuresql"
    capabilities = replace(
        SqlServerDialect.capabilities,
        cross_database=False,
        managed_service=True,
        bulk_insert_from_file=False,
    )


def connect(dsn: str, attempts: int = 5) -> SqlServerConnection:
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "SQL Server support needs pyodbc; install pgproof[sqlserver]"
        ) from exc

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return SqlServerConnection(pyodbc.connect(dsn))
        except pyodbc.Error as exc:
            last = exc
            state = str(exc.args[0]) if exc.args else ""
            if state not in TRANSIENT_SQLSTATES or attempt == attempts - 1:
                raise
            time.sleep(min(30, 2**attempt))
    assert last is not None
    raise last


__all__ = ["AzureSqlDialect", "connect"]
