"""Ports: the abstractions the domain and application layers depend on.

Adapters implement these protocols. Nothing in ``core`` or ``application``
imports a database driver, a CLI framework or a file format.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .invariants import Invariant
from .ir import Catalog, Table

if TYPE_CHECKING:
    from .report import Report


@runtime_checkable
class CatalogReader(Protocol):
    def read(self) -> Catalog: ...


@runtime_checkable
class DdlLoader(Protocol):
    def load(self, sql: str) -> Catalog: ...


@runtime_checkable
class RowWriter(Protocol):
    def truncate(self, tables: list[Table]) -> None: ...

    def write(
        self,
        table: Table,
        rows: Iterable[tuple[Any, ...]],
        method: str = "copy",
        hasher: Any = None,
    ) -> int: ...

    def reset_sequences(self, tables: list[Table]) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class QueryRunner(Protocol):
    def count(self, sql: str) -> int: ...

    def sample(self, sql: str, limit: int) -> list[dict[str, Any]]: ...

    def identity(self) -> tuple[str, str]: ...

    def unvalidated_constraints(self) -> list[str]: ...

    def table_counts(self, keys: list[str]) -> dict[str, int]: ...

    def dataset_digest(self, tables: list[Table]) -> str: ...


@runtime_checkable
class SqlExecutor(Protocol):
    def execute(self, sql: str) -> int: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class SpecLoader(Protocol):
    def load(self, path: str) -> list[Invariant]: ...


@runtime_checkable
class ReportRenderer(Protocol):
    extension: str

    def render(self, report: Report) -> str: ...


@runtime_checkable
class ReportWriter(Protocol):
    def write(self, report: Report, directory: str) -> list[str]: ...
