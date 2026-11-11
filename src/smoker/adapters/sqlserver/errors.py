"""Translate pyodbc diagnostics into domain errors."""

from __future__ import annotations

import re
from typing import Any

from ...core.errors import LoadError

_CONSTRAINT = re.compile(
    r"(?:constraint|restricción)\s+['\"]([^'\"]+)['\"]", re.IGNORECASE
)


def from_pyodbc(
    exc: Exception,
    table: str,
    row_index: int | None = None,
    columns: list[str] | None = None,
    row: list[Any] | None = None,
) -> LoadError:
    args = getattr(exc, "args", ())
    sqlstate = str(args[0]) if len(args) > 0 and args[0] else None
    message = str(args[1]) if len(args) > 1 and args[1] else str(exc).strip()
    match = _CONSTRAINT.search(message)
    return LoadError(
        message=message,
        table=table,
        constraint=match.group(1) if match else None,
        sqlstate=sqlstate,
        row_index=row_index,
        columns=list(columns or []),
        row=list(row) if row is not None else None,
    )


__all__ = ["from_pyodbc"]
