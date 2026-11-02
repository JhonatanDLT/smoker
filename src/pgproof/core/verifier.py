"""Invariant verification.

The verifier is deliberately independent from the generator: it receives a
connection and a list of invariants, and counts violations. It never trusts the
row count the generator reports.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as time_value
from decimal import Decimal
from typing import Any

import psycopg

from .invariants import Invariant, InvariantStatus


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


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime, time_value)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def verify_all(
    conn: psycopg.Connection,
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
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM ({sql}) AS violations")
                violations = int(cur.fetchone()[0])
                sample: list[dict[str, Any]] = []
                if violations > 0 and sample_limit > 0:
                    cur.execute(f"SELECT * FROM ({sql}) AS violations LIMIT %s", (sample_limit,))
                    names = [description.name for description in cur.description]
                    sample = [
                        {name: _jsonable(value) for name, value in zip(names, row)}
                        for row in cur.fetchall()
                    ]
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

    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_setting('server_version')")
        database, version = cur.fetchone()

    return Verification(
        results=results,
        duration_seconds=round(time.perf_counter() - started, 3),
        database=database,
        server_version=version,
    )


def structural_warnings(conn: psycopg.Connection) -> list[str]:
    warnings: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname || '.' || c.relname || ':' || con.conname
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE NOT con.convalidated
              AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY 1
            """
        )
        for (name,) in cur.fetchall():
            warnings.append(f"constraint {name} is NOT VALID and has not been checked on existing rows")
    return warnings
