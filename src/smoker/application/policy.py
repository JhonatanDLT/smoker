"""Exit policy for CI.

The report is always written. Whether the process fails is a separate decision
that depends on how much trust the team has in the tool yet.
"""

from __future__ import annotations

from enum import Enum

from ..core.report import Report


class FailPolicy(str, Enum):
    NEVER = "never"  # shadow mode: always exit 0, still write the report
    ERROR = "error"  # fail only when verification could not run
    FAIL = "fail"  # fail on any violation or error (default)


def should_fail(report: Report, policy: FailPolicy | str = FailPolicy.FAIL) -> bool:
    chosen = FailPolicy(policy) if isinstance(policy, str) else policy
    if chosen is FailPolicy.NEVER:
        return False
    summary = report.summary()
    if chosen is FailPolicy.ERROR:
        return summary["error"] > 0
    return report.verdict != "PASS"
