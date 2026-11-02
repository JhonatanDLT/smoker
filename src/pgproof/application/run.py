"""RunPipeline use case: generate, repair, verify."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core.repair import RepairResult
from ..core.report import Report
from .generate import GenerateDataset, GenerateResult
from .repair import RepairDataset
from .verify import VerifyDataset, VerifyRequest


@dataclass
class PipelineResult:
    report: Report
    generated: GenerateResult
    repairs: RepairResult


class RunPipeline:
    def __init__(
        self,
        generate: GenerateDataset,
        verify: VerifyDataset,
        repair: RepairDataset,
    ) -> None:
        self.generate = generate
        self.verify = verify
        self.repair = repair

    def execute(self, request: VerifyRequest) -> PipelineResult:
        invariants = self.verify.invariants_for(request.spec_path)
        generated = self.generate.execute()
        repairs = self.repair.execute(invariants)
        dataset_hash = (
            self.verify.dataset_digest() if repairs.applied else generated.dataset_hash
        )
        report = self.verify.execute(
            replace(
                request,
                invariants=invariants,
                repairs=repairs,
                dataset_hash=dataset_hash,
                plan=generated.plan,
            )
        )
        return PipelineResult(report=report, generated=generated, repairs=repairs)
