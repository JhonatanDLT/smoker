"""T-SQL dump loading is the next SQL Server milestone."""

from __future__ import annotations

from ...core.ir import Catalog


class SqlServerDdlLoader:
    def load(self, sql: str) -> Catalog:
        raise NotImplementedError(
            "T-SQL dump loading is not implemented yet; use a live SQL Server "
            "connection with --dsn for now"
        )


__all__ = ["SqlServerDdlLoader"]
