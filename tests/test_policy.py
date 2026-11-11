from __future__ import annotations

from smoker.application.policy import FailPolicy, should_fail
from smoker.core.report import Report
from smoker.core.verifier import InvariantResult


def report_with(*statuses: str) -> Report:
    results = [
        InvariantResult(
            id=f"{status}:t",
            kind="check",
            table="t",
            description=status,
            status=status,
        )
        for status in statuses
    ]
    verdict = "PASS" if all(status == "pass" for status in statuses) else "FAIL"
    return Report(
        verdict=verdict,
        created_at="2026-11-02T00:00:00Z",
        seed=1,
        database="db",
        server_version="16",
        schema_hash="a" * 64,
        dataset_hash="b" * 64,
        duration_seconds=0.1,
        table_counts={},
        results=results,
        total_rows=0,
    )


def test_fail_policy_never_always_passes() -> None:
    assert should_fail(report_with("fail"), FailPolicy.NEVER) is False


def test_fail_policy_error_ignores_violations() -> None:
    assert should_fail(report_with("fail"), FailPolicy.ERROR) is False
    assert should_fail(report_with("error"), FailPolicy.ERROR) is True


def test_fail_policy_fail_is_the_default() -> None:
    assert should_fail(report_with("pass")) is False
    assert should_fail(report_with("fail")) is True
    assert should_fail(report_with("error")) is True
    assert should_fail(report_with("fail"), "fail") is True
