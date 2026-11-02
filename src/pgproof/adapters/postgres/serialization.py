"""Canonical text serialization for PostgreSQL COPY and data digests.

The same function is used to write rows and to hash them, so the digest
reflects exactly what was inserted.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def copy_text(value: Any) -> str:
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
