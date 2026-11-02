"""Invariant verification.

The verifier is pure: it depends on the ``QueryRunner`` port, never on psycopg.
That is what lets us run the same verification against PostgreSQL today and
SQL Server later without touching the logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .invariants import Invariant, InvariantStatus
from .ports import QueryRunner


@dataclass
class InvariantResult:
    id: str
    kind: str
    table: str
    description: str
    status: str
    violations: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)
    sql: str = ""
    error: str | None = None
    note: str | None = None


@dataclass
class Verification:
    results: list[InvariantResult]
    duration_seconds: float
    database: str = ""
    server_version: str = ""

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == "fail")

    @property
    def errored(self) -> int:
        return sum(1 for result in self.results if result.status == "error")

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.status == "pass")

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.status == "skipped")

    @property
    def verdict(self) -> str:
        return "PASS" if self.failed == 0 and self.errored == 0 else "FAIL"


def verify_all(
    runner: QueryRunner,
    invariants: list[Invariant],
    sample_limit: int = 5,
) -> Verification:
    started = time.perf_counter()
    results: list[InvariantResult] = []

    for invariant in invariants:
        if invariant.status == InvariantStatus.SKIPPED or not invariant.violation_sql.strip():
            results.append(
                InvariantResult(
                    id=invariant.id,
                    kind=invariant.kind.value,
                    table=invariant.table,
                    description=invariant.description,
                    status="skipped",
                    sql=invariant.violation_sql,
                    note=invariant.note,
                )
            )
            continue

        sql = invariant.violation_sql.strip().rstrip(";")
        try:
            violations = runner.count(sql)
            sample = runner.sample(sql, sample_limit) if violations > 0 and sample_limit > 0 else []
            results.append(
                InvariantResult(
                    id=invariant.id,
                    kind=invariant.kind.value,
                    table=invariant.table,
                    description=invariant.description,
                    status="fail" if violations else "pass",
                    violations=violations,
                    sample=sample,
                    sql=sql,
                    note=invariant.note,
                )
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash a whole run
            results.append(
                InvariantResult(
                    id=invariant.id,
                    kind=invariant.kind.value,
                    table=invariant.table,
                    description=invariant.description,
                    status="error",
                    sql=sql,
                    error=str(exc).strip(),
                    note=invariant.note,
                )
            )

    database, version = runner.identity()
    return Verification(
        results=results,
        duration_seconds=round(time.perf_counter() - started, 3),
        database=database,
        server_version=version,
    )
