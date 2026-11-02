"""Domain errors.

These carry enough structure for the CLI, a CI log or a JSON consumer to explain
what failed without parsing a PostgreSQL message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoadError(RuntimeError):
    message: str
    table: str
    constraint: str | None = None
    sqlstate: str | None = None
    row_index: int | None = None
    columns: list[str] = field(default_factory=list)
    row: list[Any] | None = None
    detail: str | None = None
    hint: str | None = None
    context: str | None = None
    column: str | None = None

    def __str__(self) -> str:
        parts = [f"{self.table}: {self.message}"]
        if self.constraint:
            parts.append(f"constraint={self.constraint}")
        if self.sqlstate:
            parts.append(f"sqlstate={self.sqlstate}")
        if self.row_index is not None:
            parts.append(f"row={self.row_index + 1}")
        if self.column:
            parts.append(f"column={self.column}")
        return " · ".join(parts)

    def row_dict(self) -> dict[str, Any] | None:
        if self.row is None:
            return None
        names = self.columns or [f"column_{index + 1}" for index in range(len(self.row))]
        return {name: _display(value) for name, value in zip(names, self.row)}

    def format(self) -> str:
        lines = [f"load failed in {self.table}", f"  reason:     {self.message}"]
        if self.constraint:
            lines.append(f"  constraint: {self.constraint}")
        if self.sqlstate:
            lines.append(f"  sqlstate:   {self.sqlstate}")
        if self.row_index is not None:
            lines.append(f"  row:        {self.row_index + 1}")
        if self.column:
            lines.append(f"  column:     {self.column}")
        row = self.row_dict()
        if row:
            lines.append("  values:     " + ", ".join(f"{key}={value}" for key, value in row.items()))
        if self.detail:
            lines.append(f"  detail:     {self.detail}")
        if self.hint:
            lines.append(f"  hint:       {self.hint}")
        if self.context:
            lines.append(f"  context:    {self.context}")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "message": self.message,
            "constraint": self.constraint,
            "sqlstate": self.sqlstate,
            "row_index": self.row_index,
            "column": self.column,
            "values": self.row_dict(),
            "detail": self.detail,
            "hint": self.hint,
            "context": self.context,
        }


def _display(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


class RepairError(RuntimeError):
    def __init__(self, invariant_id: str, message: str) -> None:
        self.invariant_id = invariant_id
        self.message = message
        super().__init__(f"repair {invariant_id} failed: {message}")
