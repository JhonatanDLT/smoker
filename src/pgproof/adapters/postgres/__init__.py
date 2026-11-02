"""PostgreSQL adapter."""

from .introspect import connect, introspect, table_counts
from .profile import profile_catalog
from .writer import reset_sequences, truncate, write_table

__all__ = [
    "connect",
    "introspect",
    "table_counts",
    "profile_catalog",
    "reset_sequences",
    "truncate",
    "write_table",
]
