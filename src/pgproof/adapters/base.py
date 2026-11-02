"""Dialect adapter contract.

PostgreSQL is the only implementation in v1. SQL Server, MySQL and Oracle will
plug in here without touching the core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..core.ir import Catalog


@dataclass
class Capabilities:
    deferrable_fk: bool = False
    generated_columns: bool = False
    identity_columns: bool = False
    exclusion_constraints: bool = False
    disable_triggers: bool = False


@runtime_checkable
class DialectAdapter(Protocol):
    name: str

    def introspect(self, conn: Any) -> Catalog: ...

    def profile(self, conn: Any, table: Any, sample: int) -> Any: ...

    def write(self, conn: Any, table: Any, rows: Any, method: str) -> int: ...

    def capabilities(self) -> Capabilities: ...
