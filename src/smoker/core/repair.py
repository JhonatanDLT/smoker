"""Repair domain objects.

A repair is a deterministic SQL statement declared next to an application rule.
The report records what ran, how many rows it touched, and whether the rule
converged or left residual violations.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RepairAction:
    id: str
    table: str
    pass_number: int
    before: int
    after: int
    rows_affected: int


@dataclass
class RepairResult:
    actions: list[RepairAction] = field(default_factory=list)
    passes: int = 0
    residual: int = 0

    @property
    def applied(self) -> int:
        return sum(action.rows_affected for action in self.actions if action.rows_affected > 0)

    @property
    def improved(self) -> bool:
        return any(action.after < action.before for action in self.actions)
