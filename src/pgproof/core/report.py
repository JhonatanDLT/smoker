"""Verification report model.

This module is the canonical data structure. Rendering lives in
``adapters/report`` so a new format (JUnit XML, SARIF, Markdown) is a new
renderer, not a change to the domain.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .ir import Catalog, to_plain
from .planner import Plan
from .repair import RepairResult
from .verifier import InvariantResult, Verification

DEFAULT_LIMITATIONS = [
    "Triggers are inventoried but their side effects are not replayed or verified in v1.",
    "Exclusion constraints are reported but not generated against.",
    (
        "CHECK constraints that cannot be parsed are enforced by PostgreSQL at load time, "
        "not pre-validated by pgproof."
    ),    "Profiles are computed from a sample, not from the full table.",
    "Application-level rules are only verified when declared in the spec file.",
    "Cross-table rules (parent.total = SUM(child.amount)) are verified but not repaired.",
]


@dataclass
class Report:
    verdict: str
    created_at: str
    seed: int
    database: str
    server_version: str
    schema_hash: str
    dataset_hash: str
    duration_seconds: float
    table_counts: dict[str, int]
    results: list[InvariantResult]
    warnings: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=lambda: list(DEFAULT_LIMITATIONS))
    command: str = ""
    total_rows: int = 0
    repairs: RepairResult | None = None

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.status == "pass"),
            "failed": sum(1 for r in self.results if r.status == "fail"),
            "error": sum(1 for r in self.results if r.status == "error"),
            "skipped": sum(1 for r in self.results if r.status == "skipped"),
            "violations": sum(r.violations for r in self.results),
        }

    def to_dict(self) -> dict[str, Any]:
        repairs = (
            {
                **to_plain(self.repairs),
                "applied": self.repairs.applied,
                "improved": self.repairs.improved,
            }
            if self.repairs
            else {
                "actions": [],
                "passes": 0,
                "residual": 0,
                "applied": 0,
                "improved": False,
            }
        )
        return {
            "tool": "pgproof",
            "format_version": 2,
            "verdict": self.verdict,
            "created_at": self.created_at,
            "seed": self.seed,
            "database": self.database,
            "server_version": self.server_version,
            "schema_hash": self.schema_hash,
            "dataset_hash": self.dataset_hash,
            "duration_seconds": self.duration_seconds,
            "total_rows": self.total_rows,
            "table_counts": self.table_counts,
            "summary": self.summary(),
            "invariants": [
                {
                    "id": result.id,
                    "kind": result.kind,
                    "table": result.table,
                    "description": result.description,
                    "status": result.status,
                    "violations": result.violations,
                    "sql": result.sql,
                    "error": result.error,
                    "note": result.note,
                    "sample": result.sample,
                }
                for result in self.results
            ],
            "warnings": self.warnings,
            "limitations": self.limitations,
            "reproduce": self.command,
            "repairs": repairs,
        }


def build_report(
    catalog: Catalog,
    verification: Verification,
    plan: Plan,
    table_counts: dict[str, int],
    warnings: list[str],
    command: str = "",
    dataset_hash: str | None = None,
    repairs: RepairResult | None = None,
) -> Report:
    schema_hash = hashlib.sha256(
        json.dumps(catalog.to_dict(), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if dataset_hash is None:
        dataset_hash = hashlib.sha256(
            json.dumps({"seed": plan.seed, "counts": table_counts}, sort_keys=True).encode()
        ).hexdigest()

    return Report(
        verdict=verification.verdict,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        seed=plan.seed,
        database=verification.database,
        server_version=verification.server_version,
        schema_hash=schema_hash,
        dataset_hash=dataset_hash,
        duration_seconds=verification.duration_seconds,
        table_counts=table_counts,
        results=verification.results,
        warnings=warnings,
        command=command,
        total_rows=sum(table_counts.values()),
        repairs=repairs,
    )
