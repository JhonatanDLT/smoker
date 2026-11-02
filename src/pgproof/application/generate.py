"""GenerateDataset use case.

Orchestrates planning, generation, writing and diagnostic enrichment. It knows
about ports (RowWriter) and domain objects, never about psycopg or typer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice

from ..core.errors import LoadError
from ..core.generator import GenerationError, Generator
from ..core.ir import Catalog, Table
from ..core.planner import Plan, build_plan
from ..core.ports import RowWriter

Progress = Callable[[str, int, float], None]


def _noop_progress(key: str, rows: int, elapsed: float) -> None:
    return None


@dataclass
class GenerateResult:
    plan: Plan
    counts: dict[str, int]
    dataset_hash: str


class GenerateDataset:
    def __init__(
        self,
        catalog: Catalog,
        writer: RowWriter,
        rows: int = 1000,
        table_rows: dict[str, int] | None = None,
        seed: int = 42,
        method: str = "copy",
        truncate: bool = True,
        progress: Progress | None = None,
    ) -> None:
        self.catalog = catalog
        self.writer = writer
        self.rows = rows
        self.table_rows = table_rows
        self.seed = seed
        self.method = method
        self.truncate = truncate
        self.progress = progress or _noop_progress

    def execute(self) -> GenerateResult:
        plan = build_plan(
            self.catalog,
            rows=self.rows,
            table_rows=self.table_rows,
            seed=self.seed,
        )
        return self.execute_plan(plan)

    def execute_plan(self, plan: Plan) -> GenerateResult:
        import time

        generator = Generator(self.catalog, plan)
        hasher = hashlib.sha256()
        counts: dict[str, int] = {}
        try:
            if self.truncate:
                self.writer.truncate(self.catalog.tables)
            for key in plan.order:
                table = self.catalog.table(key)
                started = time.perf_counter()
                try:
                    written = self.writer.write(
                        table,
                        generator.generate_validated(table),
                        method=self.method,
                        hasher=hasher,
                    )
                except LoadError as exc:
                    self._enrich(exc, table, plan)
                    raise
                counts[key] = written
                self.progress(key, written, time.perf_counter() - started)
            self.writer.reset_sequences(self.catalog.tables)
            self.writer.commit()
        except Exception:
            self.writer.rollback()
            raise
        return GenerateResult(plan=plan, counts=counts, dataset_hash=hasher.hexdigest())

    def _enrich(self, error: LoadError, table: Table, plan: Plan) -> None:
        """Recover the offending row when PostgreSQL only reported a COPY line."""
        if error.row is not None or error.row_index is None:
            return
        try:
            replay = Generator(self.catalog, plan)
            rows = list(islice(replay.generate_validated(table), error.row_index + 1))
        except GenerationError:
            return
        if len(rows) == error.row_index + 1:
            error.columns = [column.name for column in table.writable_columns]
            error.row = list(rows[-1])
