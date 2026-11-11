"""Invariant verification.

The verifier is pure: it depends on the ``QueryRunner`` port, never on psycopg.
That is what lets us run the same verification against PostgreSQL today and
SQL Server later without touching the logic.

Checks are independent, so ``verify_all`` can spread them across several
connections. Every worker owns one runner, the report keeps catalog order and
the verdict is unchanged by the worker count.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
    provenance: str | None = None


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


def _skipped(invariant: Invariant) -> InvariantResult:
    return InvariantResult(
        id=invariant.id,
        kind=invariant.kind.value,
        table=invariant.table,
        description=invariant.description,
        status="skipped",
        sql=invariant.violation_sql,
        note=invariant.note,
        provenance=invariant.provenance,
    )


def _verify_one(
    runner: QueryRunner, invariant: Invariant, sample_limit: int
) -> InvariantResult:
    sql = invariant.violation_sql.strip().rstrip(";")
    try:
        violations = runner.count(sql)
        sample = (
            runner.sample(sql, sample_limit)
            if violations > 0 and sample_limit > 0
            else []
        )
        return InvariantResult(
            id=invariant.id,
            kind=invariant.kind.value,
            table=invariant.table,
            description=invariant.description,
            status="fail" if violations else "pass",
            violations=violations,
            sample=sample,
            sql=sql,
            note=invariant.note,
            provenance=invariant.provenance,
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash a whole run
        return InvariantResult(
            id=invariant.id,
            kind=invariant.kind.value,
            table=invariant.table,
            description=invariant.description,
            status="error",
            sql=sql,
            error=str(exc).strip(),
            note=invariant.note,
            provenance=invariant.provenance,
        )


def _verify_chunk(
    runner: QueryRunner,
    invariants: list[Invariant],
    sample_limit: int,
) -> list[InvariantResult]:
    return [_verify_one(runner, invariant, sample_limit) for invariant in invariants]


def verify_all(
    runner: QueryRunner,
    invariants: list[Invariant],
    sample_limit: int = 5,
    runner_factory: Callable[[], QueryRunner] | None = None,
    workers: int = 1,
) -> Verification:
    started = time.perf_counter()
    pending = [
        invariant
        for invariant in invariants
        if invariant.status != InvariantStatus.SKIPPED
        and invariant.violation_sql.strip()
    ]
    skipped = {
        invariant.id: _skipped(invariant)
        for invariant in invariants
        if invariant.status == InvariantStatus.SKIPPED
        or not invariant.violation_sql.strip()
    }

    workers = max(1, int(workers))
    parallel = workers > 1 and runner_factory is not None and len(pending) > 1
    results: dict[str, InvariantResult] = {}
    owned: list[QueryRunner] = []
    if parallel:
        pool_size = min(workers, len(pending))
        try:
            for _ in range(pool_size):
                owned.append(runner_factory())
        except Exception:  # noqa: BLE001 - fall back to the primary connection
            for owned_runner in owned:
                close = getattr(owned_runner, "close", None)
                if callable(close):
                    close()
            owned = []
        if owned:
            chunks = [pending[index::pool_size] for index in range(pool_size)]
            with ThreadPoolExecutor(max_workers=pool_size) as executor:
                futures = [
                    executor.submit(_verify_chunk, owned[index], chunk, sample_limit)
                    for index, chunk in enumerate(chunks)
                ]
                for future in futures:
                    for result in future.result():
                        results[result.id] = result
        else:
            parallel = False

    if not parallel:
        for invariant in pending:
            result = _verify_one(runner, invariant, sample_limit)
            results[result.id] = result

    for owned_runner in owned:
        close = getattr(owned_runner, "close", None)
        if callable(close):
            close()

    ordered = [
        results.get(invariant.id) or skipped[invariant.id] for invariant in invariants
    ]

    database, version = runner.identity()
    return Verification(
        results=ordered,
        duration_seconds=round(time.perf_counter() - started, 3),
        database=database,
        server_version=version,
    )
