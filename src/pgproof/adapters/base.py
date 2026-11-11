"""Aggregate contract for a database adapter.

A dialect is pure SQL knowledge. An adapter bundles it with the connection-bound
implementations: catalog reader, writer, query runner, executor, profiler and
DDL loader. The CLI wires one adapter and the application only sees ports.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..core.dialect import Capabilities, SqlDialect
from ..core.ir import Catalog, Table
from ..core.ports import DdlLoader, QueryRunner, RowWriter, SqlExecutor
from ..core.profile import Profile


@dataclass(frozen=True)
class DatabaseAdapter:
    name: str
    dialect: SqlDialect
    ddl_loader: DdlLoader
    connect: Callable[[str], Any]
    introspect: Callable[[Any], Catalog]
    writer: Callable[[Any], RowWriter]
    query_runner: Callable[[Any], QueryRunner]
    executor: Callable[[Any], SqlExecutor]
    profiler: Callable[..., Profile]

    def profile(
        self,
        connection: Any,
        tables: list[Table],
        sample: int = 1000,
        method: str = "auto",
        seed: int = 42,
    ) -> Profile:
        return self.profiler(
            connection, tables, sample=sample, method=method, seed=seed
        )


__all__ = ["Capabilities", "DatabaseAdapter", "SqlDialect"]
