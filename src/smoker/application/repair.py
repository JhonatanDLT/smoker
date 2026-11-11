"""RepairDataset use case.

Runs the deterministic repair statements declared in the spec, up to a fixed
number of passes, and records what changed. It never invents repairs: if an
invariant has no ``repair_sql``, it is verified but left untouched.
"""

from __future__ import annotations

from ..core.errors import RepairError
from ..core.invariants import Invariant
from ..core.ports import QueryRunner, SqlExecutor
from ..core.repair import RepairAction, RepairResult


class RepairDataset:
    def __init__(
        self,
        runner: QueryRunner,
        executor: SqlExecutor,
        max_passes: int = 3,
    ) -> None:
        self.runner = runner
        self.executor = executor
        self.max_passes = max(1, max_passes)

    def execute(self, invariants: list[Invariant]) -> RepairResult:
        repairable = [
            invariant
            for invariant in invariants
            if invariant.repair_sql and invariant.violation_sql.strip()
        ]
        if not repairable:
            return RepairResult()

        actions: list[RepairAction] = []
        passes_used = 0

        for pass_number in range(1, self.max_passes + 1):
            acted = False
            changed = False
            for invariant in repairable:
                before = self.runner.count(invariant.violation_sql)
                if before == 0:
                    continue
                acted = True
                try:
                    rows = self.executor.execute(invariant.repair_sql or "")
                except Exception as exc:
                    self.executor.rollback()
                    raise RepairError(invariant.id, str(exc).strip()) from exc
                after = self.runner.count(invariant.violation_sql)
                actions.append(
                    RepairAction(
                        id=invariant.id,
                        table=invariant.table,
                        pass_number=pass_number,
                        before=before,
                        after=after,
                        rows_affected=rows,
                    )
                )
                if rows > 0 or after < before:
                    changed = True
            if acted:
                passes_used += 1
            if not changed:
                break

        self.executor.commit()
        residual = sum(self.runner.count(invariant.violation_sql) for invariant in repairable)
        return RepairResult(actions=actions, passes=passes_used, residual=residual)
