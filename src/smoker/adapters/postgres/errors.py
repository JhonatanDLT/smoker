"""Translate PostgreSQL diagnostics into domain errors."""

from __future__ import annotations

import re
from typing import Any

from ...core.errors import LoadError

_COPY_LINE = re.compile(r"line (\d+)", re.IGNORECASE)


def from_psycopg(
    exc: Exception,
    table: str,
    row_index: int | None = None,
    columns: list[str] | None = None,
    row: list[Any] | None = None,
) -> LoadError:
    diag = getattr(exc, "diag", None)

    def field(name: str) -> str | None:
        if diag is None:
            return None
        value = getattr(diag, name, None)
        return str(value) if value not in (None, "") else None

    message = field("message_primary") or str(exc).strip()
    context = field("context")
    haystack = " ".join(part for part in (str(exc), context or "") if part)
    match = _COPY_LINE.search(haystack)
    line = int(match.group(1)) if match else None

    if row_index is None and line is not None:
        row_index = line - 1

    return LoadError(
        message=message,
        table=table,
        constraint=field("constraint_name"),
        sqlstate=field("sqlstate") or getattr(exc, "sqlstate", None),
        row_index=row_index,
        columns=list(columns or []),
        row=list(row) if row is not None else None,
        detail=field("message_detail"),
        hint=field("message_hint"),
        context=context,
        column=field("column_name"),
    )
