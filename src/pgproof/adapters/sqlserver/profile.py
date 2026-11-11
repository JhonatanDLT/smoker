"""SQL Server profiling is planned; this placeholder fails clearly."""

from __future__ import annotations

from typing import Any

from ...core.ir import Table
from ...core.profile import Profile


def profile_catalog(
    connection: Any,
    tables: list[Table],
    sample: int = 1000,
    method: str = "auto",
    seed: int = 42,
) -> Profile:
    raise NotImplementedError(
        "SQL Server profiling is not implemented yet; run `pgproof profile` "
        "against PostgreSQL or wait for the SQL Server profiler"
    )


__all__ = ["profile_catalog"]
