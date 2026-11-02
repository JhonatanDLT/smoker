"""RunPipeline use case: generate, then verify, with the same plan and hash."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core.report import Report
from .generate import GenerateDataset, GenerateResult
from .verify import VerifyDataset, VerifyRequest


@dataclass
class PipelineResult:
    report: Report
    generated: GenerateResult


class RunPipeline:
    def __init__(self, generate: GenerateDataset, verify: VerifyDataset) -> None:
        self.generate = generate
        self.verify = verify

    def execute(self, request: VerifyRequest) -> PipelineResult:
        generated = self.generate.execute()
        report = self.verify.execute(
            replace(
                request,
                dataset_hash=generated.dataset_hash,
                plan=generated.plan,
            )
        )
        return PipelineResult(report=report, generated=generated)
