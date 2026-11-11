"""Database dialect contract.

A dialect is pure: it knows how to quote identifiers, render literals, map
types and build the SQL that verifies table constraints. It never opens a
connection and never imports a driver.

Adapters implement this protocol. The core defines it, so the dependency rule
stays intact: core <- adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .ir import Column, DataKind, ExclusionConstraint, ForeignKey, Table


@dataclass(frozen=True)
class Capabilities:
    """What a dialect and its engine can actually do.

    Behaviour is selected from these flags, never from the dialect name.
    """

    exclusion_constraints: bool = False
    native_ranges: bool = False
    native_enums: bool = False
    arrays: bool = False
    partition_model: str = "none"  # declarative | scheme | none
    identity: bool = False
    identity_insert: bool = False
    computed_columns: bool = False
    rowversion: bool = False
    sequences: bool = False
    truncate_cascade: bool = False
    row_identity: str = "pk"  # pk | ctid | physloc
    not_valid_constraints: bool = False
    filtered_indexes: bool = False
    temporal_tables: bool = False
    bulk_method: str = "batch"  # copy | bulk_insert | batch
    sample_method: str = "limit"  # tablesample | limit | top


@runtime_checkable
class SqlDialect(Protocol):
    name: str
    capabilities: Capabilities

    # ---------------------------------------------------------- primitives
    def quote_ident(self, name: str) -> str: ...

    def quote_qualified(self, schema: str, name: str) -> str: ...

    def literal(self, value: Any) -> str: ...

    def classify_type(
        self, sql_type: str, enum_values: list[str] | None = None
    ) -> DataKind: ...

    # ------------------------------------------------------ invariant SQL
    def not_null_sql(self, table: Table, column: Column) -> str: ...

    def enum_sql(self, table: Table, column: Column) -> str: ...

    def unique_sql(
        self, table: Table, columns: list[str], predicate: str | None = None
    ) -> str: ...

    def foreign_key_sql(self, table: Table, fk: ForeignKey) -> str: ...

    def check_sql(self, table: Table, predicate: str) -> str: ...

    def exclusion_sql(
        self, table: Table, exclusion: ExclusionConstraint
    ) -> str | None: ...

    # ------------------------------------------------------ query wrapping
    def count_sql(self, sql: str) -> str: ...

    def sample_sql(self, sql: str, limit: int) -> str: ...

    # ------------------------------------------------------ SQL scripts
    def script_begin(self) -> list[str]: ...

    def script_commit(self) -> list[str]: ...

    def script_rollback(self) -> list[str]: ...

    def script_truncate(self, tables: list[Table]) -> list[str]: ...

    def script_reset_sequences(self, tables: list[Table]) -> list[str]: ...

    def script_before_insert(self, table: Table) -> list[str]: ...

    def script_after_insert(self, table: Table) -> list[str]: ...
