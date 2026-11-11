"""File-system report writer.

Takes a list of renderers, so adding a format is a configuration change.
"""

from __future__ import annotations

from pathlib import Path

from ...core.ports import ReportRenderer
from ...core.report import Report


class FileReportWriter:
    def __init__(self, renderers: list[ReportRenderer]) -> None:
        self.renderers = renderers

    def write(self, report: Report, directory: str) -> list[str]:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for renderer in self.renderers:
            path = target / f"report.{renderer.extension}"
            path.write_text(renderer.render(report), encoding="utf-8")
            written.append(str(path))
        return written
