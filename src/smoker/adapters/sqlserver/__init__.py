"""SQL Server adapter (experimental).

The dialect is pure and can be used without a driver. Connection-bound
components import ``pyodbc`` lazily so PostgreSQL-only installs keep working.
"""

from .dialect import SqlServerDialect

__all__ = ["SqlServerDialect"]
