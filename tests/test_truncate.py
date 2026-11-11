from __future__ import annotations

from typing import Self

from smoker.adapters.postgres.writer import truncate
from smoker.core.ir import Table


class FakeCursor:
    def __init__(self, statements: list[str]) -> None:
        self.statements = statements

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args) -> bool:
        return False

    def execute(self, sql: str) -> None:
        self.statements.append(sql)


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.statements)


def tables(count: int) -> list[Table]:
    return [Table(schema="public", name=f"t{index:03d}") for index in range(count)]


def test_truncate_splits_into_batches() -> None:
    connection = FakeConnection()
    truncate(connection, tables(250), batch_size=100)

    assert len(connection.statements) == 3
    assert "t000" in connection.statements[0]
    assert "t099" in connection.statements[0]
    assert "t100" not in connection.statements[0]
    assert "t200" in connection.statements[2]
    assert "t249" in connection.statements[2]


def test_truncate_handles_empty_input() -> None:
    connection = FakeConnection()
    truncate(connection, [])
    assert connection.statements == []


def test_truncate_keeps_cascade() -> None:
    connection = FakeConnection()
    truncate(connection, tables(2), cascade=True)
    assert connection.statements[0].endswith("CASCADE")

    connection = FakeConnection()
    truncate(connection, tables(2), cascade=False)
    assert not connection.statements[0].endswith("CASCADE")
