"""PostgreSQL adapter.

The public surface is the set of classes that implement the domain ports plus
the introspection helper.
"""

from .introspect import connect, introspect, table_counts
from .profile import profile_catalog
from .query_runner import PostgresQueryRunner
from .sql_executor import PostgresSqlExecutor
from .writer import PostgresRowWriter, reset_sequences, truncate, write_table

__all__ = [
    "PostgresQueryRunner",
    "PostgresRowWriter",
    "PostgresSqlExecutor",
    "connect",
    "introspect",
    "profile_catalog",
    "reset_sequences",
    "table_counts",
    "truncate",
    "write_table",
]
