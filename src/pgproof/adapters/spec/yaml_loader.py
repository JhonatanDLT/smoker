"""YAML implementation of the SpecLoader port.

The domain does not know what YAML is. This adapter translates a file into
domain invariants.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ...core.invariants import Invariant, InvariantKind, InvariantStatus


class SpecError(ValueError):
    pass


class YamlSpecLoader:
    def load(self, path: str) -> list[Invariant]:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw_invariants = data.get("invariants") or []
        invariants: list[Invariant] = []
        for index, raw in enumerate(raw_invariants):
            if not isinstance(raw, dict):
                raise SpecError(f"invariant #{index} must be a mapping")
            sql = (raw.get("sql") or "").strip()
            if not sql:
                raise SpecError(f"invariant #{index} is missing `sql`")
            identifier = raw.get("id") or f"app_rule:{index}"
            invariants.append(
                Invariant(
                    id=str(identifier),
                    kind=InvariantKind.APP_RULE,
                    table=str(raw.get("table", "")),
                    description=str(raw.get("description", identifier)),
                    violation_sql=sql,
                    status=InvariantStatus.DECLARED,
                    source="spec",
                    columns=list(raw.get("columns") or []),
                )
            )
        return invariants
