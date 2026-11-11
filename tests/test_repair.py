from __future__ import annotations

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from smoker.adapters.postgres.dialect import PostgresDialect
from smoker.adapters.postgres.introspect import connect, introspect
from smoker.adapters.postgres.query_runner import PostgresQueryRunner
from smoker.adapters.postgres.sql_executor import PostgresSqlExecutor
from smoker.adapters.postgres.writer import PostgresRowWriter
from smoker.adapters.spec.yaml_loader import YamlSpecLoader
from smoker.application.generate import GenerateDataset
from smoker.application.repair import RepairDataset
from smoker.application.verify import VerifyDataset, VerifyRequest
from smoker.core.errors import RepairError
from smoker.core.invariants import Invariant, InvariantKind

DSN = os.environ.get("SMOKER_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    def count(self, sql: str) -> int:
        return self.counts.get(sql, 0)

    def sample(self, sql: str, limit: int) -> list[dict]:
        return []

    def identity(self) -> tuple[str, str]:
        return "fake", "16"

    def unvalidated_constraints(self) -> list[str]:
        return []

    def table_counts(self, keys: list[str]) -> dict[str, int]:
        return {}

    def dataset_digest(self, tables) -> str:
        return "0" * 64


class FakeExecutor:
    def __init__(self, runner: FakeRunner, effect, fail: bool = False) -> None:
        self.runner = runner
        self.effect = effect
        self.fail = fail
        self.committed = False
        self.rolled_back = False

    def execute(self, sql: str) -> int:
        if self.fail:
            raise RuntimeError("syntax error near UPDATE")
        return self.effect(self.runner, sql)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def invariant(repair: str | None = "REPAIR") -> Invariant:
    return Invariant(
        id="app:test",
        kind=InvariantKind.APP_RULE,
        table="public.t",
        description="test rule",
        violation_sql="VIOLATIONS",
        repair_sql=repair,
    )


def test_repair_converges_in_one_pass() -> None:
    runner = FakeRunner({"VIOLATIONS": 5})
    executor = FakeExecutor(runner, lambda r, sql: (r.counts.update({"VIOLATIONS": 0}), 5)[1])
    result = RepairDataset(runner, executor, max_passes=3).execute([invariant()])

    assert result.passes == 1
    assert result.residual == 0
    assert result.applied == 5
    assert executor.committed is True
    assert result.actions[0].before == 5
    assert result.actions[0].after == 0


def test_no_repair_sql_is_ignored() -> None:
    runner = FakeRunner({"VIOLATIONS": 5})
    executor = FakeExecutor(runner, lambda r, sql: 0)
    result = RepairDataset(runner, executor).execute([invariant(repair=None)])

    assert result.actions == []
    assert result.passes == 0
    assert executor.committed is False


def test_non_converging_repair_stops_at_max_passes() -> None:
    runner = FakeRunner({"VIOLATIONS": 5})
    executor = FakeExecutor(runner, lambda r, sql: (r.counts.update({"VIOLATIONS": r.counts["VIOLATIONS"] - 1}), 1)[1])
    result = RepairDataset(runner, executor, max_passes=3).execute([invariant()])

    assert result.passes == 3
    assert result.residual == 2
    assert len(result.actions) == 3


def test_repair_error_rolls_back() -> None:
    runner = FakeRunner({"VIOLATIONS": 1})
    executor = FakeExecutor(runner, lambda r, sql: 0, fail=True)

    with pytest.raises(RepairError) as error:
        RepairDataset(runner, executor).execute([invariant()])

    assert "syntax error" in str(error.value)
    assert executor.rolled_back is True


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="SMOKER_TEST_DSN is not set")
def test_cross_table_repair_reaches_zero() -> None:
    assert DSN
    subprocess.run(
        ["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", "-f", str(ROOT / "examples/saas-billing/schema.sql")],
        check=True,
        capture_output=True,
    )
    with connect(DSN) as conn:
        catalog = introspect(conn)

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    GenerateDataset(catalog, writer, rows=30, seed=5).execute()
    writer.close()

    spec = str(ROOT / "examples/saas-billing/plan-repair.yml")
    connection = psycopg.connect(DSN)
    try:
        runner = PostgresQueryRunner(connection)
        executor = PostgresSqlExecutor(connection)
        verify = VerifyDataset(catalog, runner, YamlSpecLoader(), PostgresDialect(), seed=5)
        invariants = verify.invariants_for(spec)
        repair = RepairDataset(runner, executor, max_passes=3).execute(invariants)

        assert repair.applied > 0
        assert repair.residual == 0

        report = verify.execute(
            VerifyRequest(
                invariants=invariants,
                repairs=repair,
                dataset_hash=verify.dataset_digest(),
            )
        )
        assert report.verdict == "PASS"
        assert len(report.dataset_hash) == 64
    finally:
        connection.close()
