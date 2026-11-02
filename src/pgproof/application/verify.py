"""VerifyDataset use case."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.invariants import invariants_from_catalog, merge_invariants
from ..core.ir import Catalog
from ..core.planner import Plan, build_plan
from ..core.ports import QueryRunner, SpecLoader
from ..core.report import Report, build_report
from ..core.verifier import verify_all


@dataclass
class VerifyRequest:
    spec_path: str | None = None
    sample: int = 5
    command: str = ""
    dataset_hash: str | None = None
    plan: Plan | None = None


class VerifyDataset:
    def __init__(
        self,
        catalog: Catalog,
        runner: QueryRunner,
        spec_loader: SpecLoader,
        seed: int = 42,
    ) -> None:
        self.catalog = catalog
        self.runner = runner
        self.spec_loader = spec_loader
        self.seed = seed

    def execute(self, request: VerifyRequest) -> Report:
        extra = self.spec_loader.load(request.spec_path) if request.spec_path else []
        invariants = merge_invariants(invariants_from_catalog(self.catalog), extra)
        verification = verify_all(self.runner, invariants, sample_limit=request.sample)
        counts = self.runner.table_counts(self.catalog.table_keys)
        warnings = self.runner.unvalidated_constraints()
        plan = request.plan or build_plan(self.catalog, rows=0, seed=self.seed)
        return build_report(
            self.catalog,
            verification,
            plan,
            counts,
            warnings,
            command=request.command,
            dataset_hash=request.dataset_hash,
        )
