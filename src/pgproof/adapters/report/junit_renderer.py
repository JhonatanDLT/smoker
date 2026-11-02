"""JUnit XML renderer.

Most CI systems ingest JUnit XML and show each invariant as a test case. This
adapter turns a Report into that format without touching the domain model.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from ...core.report import Report
from ...core.verifier import InvariantResult


class JUnitRenderer:
    extension = "xml"

    def render(self, report: Report) -> str:
        summary = report.summary()
        attributes = {
            "name": "pgproof",
            "tests": str(summary["total"]),
            "failures": str(summary["failed"]),
            "errors": str(summary["error"]),
            "skipped": str(summary["skipped"]),
            "time": f"{report.duration_seconds:.3f}",
        }
        root = ET.Element("testsuites", attributes)
        suite = ET.SubElement(root, "testsuite", attributes)

        for result in report.results:
            case = ET.SubElement(
                suite,
                "testcase",
                {
                    "classname": result.kind,
                    "name": result.id,
                    "time": "0",
                },
            )
            if result.status == "fail":
                failure = ET.SubElement(
                    case,
                    "failure",
                    {
                        "message": (
                            f"{result.description} · {result.violations} "
                            f"violating row{'s' if result.violations != 1 else ''}"
                        )
                    },
                )
                failure.text = _details(result)
            elif result.status == "error":
                error = ET.SubElement(
                    case, "error", {"message": result.error or "verification error"}
                )
                error.text = result.sql
            elif result.status == "skipped":
                ET.SubElement(
                    case, "skipped", {"message": result.note or "not verified in this version"}
                )

        ET.indent(root, space="  ")
        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
            root, encoding="unicode"
        )


def _details(result: InvariantResult) -> str:
    parts = [f"table: {result.table}", f"sql: {result.sql}"]
    if result.sample:
        parts.append("sample: " + json.dumps(result.sample, indent=2, ensure_ascii=False))
    if result.error:
        parts.append(f"error: {result.error}")
    return "\n".join(parts)
