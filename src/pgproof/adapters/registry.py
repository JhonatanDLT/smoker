"""Dialect and database-adapter registry.

The CLI is the composition root. It asks this module for a concrete adapter and
never imports a backend module directly.
"""

from __future__ import annotations

from collections.abc import Callable

from ..core.dialect import SqlDialect
from ..core.ports import DdlLoader
from .base import DatabaseAdapter
from .postgres.dialect import PostgresDialect
from .postgres.dump import DumpLoader
from .postgres.introspect import connect, introspect
from .postgres.profile import profile_catalog
from .postgres.query_runner import PostgresQueryRunner
from .postgres.sql_executor import PostgresSqlExecutor
from .postgres.writer import PostgresRowWriter
from .sqlserver.azure import AzureSqlDialect
from .sqlserver.azure import connect as azure_connect
from .sqlserver.dialect import SqlServerDialect
from .sqlserver.dump import SqlServerDumpLoader
from .sqlserver.introspect import connect as sqlserver_connect
from .sqlserver.introspect import introspect as sqlserver_introspect
from .sqlserver.profile import profile_catalog as sqlserver_profile
from .sqlserver.query_runner import SqlServerQueryRunner
from .sqlserver.sql_executor import SqlServerSqlExecutor
from .sqlserver.writer import SqlServerRowWriter


def _postgres_introspect(connection, databases=None):
    if databases:
        raise ValueError(
            "PostgreSQL does not support multiple databases in one catalog; "
            "one connection is one database"
        )
    return introspect(connection)


def _postgres_adapter() -> DatabaseAdapter:
    return DatabaseAdapter(
        name="postgres",
        dialect=PostgresDialect(),
        ddl_loader=DumpLoader(),
        connect=connect,
        introspect=_postgres_introspect,
        writer=PostgresRowWriter,
        query_runner=PostgresQueryRunner,
        executor=PostgresSqlExecutor,
        profiler=profile_catalog,
    )


def _sqlserver_adapter() -> DatabaseAdapter:
    return DatabaseAdapter(
        name="sqlserver",
        dialect=SqlServerDialect(),
        ddl_loader=SqlServerDumpLoader(),
        connect=sqlserver_connect,
        introspect=sqlserver_introspect,
        writer=SqlServerRowWriter,
        query_runner=SqlServerQueryRunner,
        executor=SqlServerSqlExecutor,
        profiler=sqlserver_profile,
    )


def _azure_introspect(connection, databases=None):
    return sqlserver_introspect(
        connection, databases=databases, dialect=AzureSqlDialect()
    )


def _azuresql_adapter() -> DatabaseAdapter:
    return DatabaseAdapter(
        name="azuresql",
        dialect=AzureSqlDialect(),
        ddl_loader=SqlServerDumpLoader(allow_cross_database=False),
        connect=azure_connect,
        introspect=_azure_introspect,
        writer=SqlServerRowWriter,
        query_runner=SqlServerQueryRunner,
        executor=SqlServerSqlExecutor,
        profiler=sqlserver_profile,
    )


_ADAPTERS: dict[str, Callable[[], DatabaseAdapter]] = {
    "postgres": _postgres_adapter,
    "sqlserver": _sqlserver_adapter,
    "azuresql": _azuresql_adapter,
}


def register_adapter(name: str, adapter: Callable[[], DatabaseAdapter]) -> None:
    """Register a database adapter at runtime (plugins and tests)."""
    _ADAPTERS[name] = adapter


def available_dialects() -> list[str]:
    return sorted(_ADAPTERS)


def get_adapter(name: str) -> DatabaseAdapter:
    factory = _ADAPTERS.get(name)
    if factory is None:
        raise ValueError(
            f"unknown dialect {name!r}; available: {', '.join(available_dialects())}"
        )
    return factory()


def get_dialect(name: str) -> SqlDialect:
    return get_adapter(name).dialect


def get_ddl_loader(name: str) -> DdlLoader:
    return get_adapter(name).ddl_loader


__all__ = [
    "available_dialects",
    "get_adapter",
    "get_ddl_loader",
    "get_dialect",
    "register_adapter",
]
