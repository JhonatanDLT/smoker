"""Schema audit model.

The audit is the lead magnet and the first deliverable of a pilot: it describes
what a schema would need before anyone generates a single row.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Risk:
    severity: str  # high | medium | low
    table: str
    finding: str


@dataclass
class Audit:
    database: str
    server_version: str
    tables: int
    columns: int
    foreign_keys: int
    checks: int
    triggers: int
    invariants: dict[str, int] = field(default_factory=dict)
    risks: list[Risk] = field(default_factory=list)

    def by_severity(self, severity: str) -> list[Risk]:
        return [risk for risk in self.risks if risk.severity == severity]

    @property
    def verdict(self) -> str:
        if self.by_severity("high"):
            return "high risk"
        if self.by_severity("medium"):
            return "medium risk"
        return "low risk"
