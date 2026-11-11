"""Parallel verification keeps order, isolates connections and closes them."""

from __future__ import annotations

import time

from pgproof.core.invariants import Invariant, InvariantKind, InvariantStatus
from pgproof.core.verifier import verify_all


class FakeRunner:
    def __init__(self) -> None:
        self.counts: list[str] = []
        self.closed = False

    def count(self, sql: str) -> int:
        self.counts.append(sql)
        time.sleep(0.01)
        if "boom" in sql:
            raise RuntimeError("query failed")
        return 1 if "bad" in sql else 0

    def sample(self, sql: str, limit: int) -> list[dict[str, object]]:
        return [{"id": 1}][:limit]

    def identity(self) -> tuple[str, str]:
        return "db", "16"

    def unvalidated_constraints(self) -> list[str]:
        return []

    def table_counts(self, keys: list[str]) -> dict[str, int]:
        return {}

    def dataset_digest(self, tables: list[object]) -> str:
        return "hash"

    def close(self) -> None:
        self.closed = True


def _invariant(index: int, sql: str | None = None, skipped: bool = False) -> Invariant:
    return Invariant(
        id=f"check:{index}",
        kind=InvariantKind.CHECK,
        table="public.t",
        description=f"check {index}",
        violation_sql=sql or f"SELECT 1 AS violation WHERE {index} = -1",
        status=InvariantStatus.SKIPPED if skipped else InvariantStatus.DECLARED,
    )


def test_parallel_verification_preserves_order_and_closes_workers() -> None:
    primary = FakeRunner()
    created: list[FakeRunner] = []

    def factory() -> FakeRunner:
        runner = FakeRunner()
        created.append(runner)
        return runner

    invariants = [
        _invariant(0),
        _invariant(1, "SELECT 1 AS violation WHERE boom"),
        _invariant(2, skipped=True),
        _invariant(3, "SELECT 1 AS violation WHERE bad"),
        _invariant(4),
        _invariant(5, "SELECT 1 AS violation WHERE boom"),
    ]

    verification = verify_all(
        primary,
        invariants,
        sample_limit=1,
        runner_factory=factory,
        workers=3,
    )

    assert [result.id for result in verification.results] == [
        f"check:{index}" for index in range(6)
    ]
    assert [result.status for result in verification.results] == [
        "pass",
        "error",
        "skipped",
        "fail",
        "pass",
        "error",
    ]
    assert verification.results[3].sample == [{"id": 1}]
    assert verification.verdict == "FAIL"
    assert verification.database == "db"
    assert primary.counts == []
    assert len(created) == 3
    assert all(runner.closed for runner in created)
    assert all(runner.counts for runner in created)


def test_single_worker_uses_the_primary_runner() -> None:
    primary = FakeRunner()
    created: list[FakeRunner] = []

    def factory() -> FakeRunner:
        runner = FakeRunner()
        created.append(runner)
        return runner

    verification = verify_all(
        primary,
        [_invariant(0), _invariant(1), _invariant(2, skipped=True)],
        runner_factory=factory,
        workers=1,
    )

    assert verification.verdict == "PASS"
    assert created == []
    assert len(primary.counts) == 2


def test_connection_failure_falls_back_to_primary() -> None:
    primary = FakeRunner()

    def factory() -> FakeRunner:
        raise RuntimeError("cannot connect")

    verification = verify_all(
        primary,
        [_invariant(0), _invariant(1)],
        runner_factory=factory,
        workers=4,
    )

    assert verification.verdict == "PASS"
    assert len(primary.counts) == 2
