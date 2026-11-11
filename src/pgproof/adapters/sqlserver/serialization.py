"""Canonical text serialization for SQL Server digests."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def text(value: Any) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    return str(value)


__all__ = ["text"]
