from __future__ import annotations

import xml.etree.ElementTree as ET

from smoker.adapters.report import JUnitRenderer
from smoker.core.report import Report
from smoker.core.verifier import InvariantResult


def make_report() -> Report:
    results = [
        InvariantResult(
            id="check:public.invoices:abc",
            kind="check",
            table="public.invoices",
            description="total is non-negative",
            status="fail",
            violations=3,
            sql="SELECT 1 FROM public.invoices WHERE (total >= 0) IS FALSE",
            sample=[{"id": 7, "total": "-10"}],
        ),
        InvariantResult(
            id="not_null:public.users:def",
            kind="not_null",
            table="public.users",
            description="email is never null",
            status="pass",
        ),
        InvariantResult(
            id="trigger:public.accounts:ghi",
            kind="trigger",
            table="public.accounts",
            description="trigger effects",
            status="skipped",
            note="trigger effects are not verified in v1",
        ),
        InvariantResult(
            id="unique:public.plans:jkl",
            kind="unique",
            table="public.plans",
            description="code is unique",
            status="error",
            error="relation does not exist",
            sql="SELECT code FROM public.plans GROUP BY code HAVING count(*) > 1",
        ),
    ]
    return Report(
        verdict="FAIL",
        created_at="2026-11-02T00:00:00Z",
        seed=42,
        database="app_test",
        server_version="16.15",
        schema_hash="a" * 64,
        dataset_hash="b" * 64,
        duration_seconds=0.42,
        table_counts={"public.invoices": 10},
        results=results,
        total_rows=10,
    )


def test_junit_counts_match_the_report() -> None:
    report = make_report()
    xml = JUnitRenderer().render(report)
    root = ET.fromstring(xml)

    assert root.tag == "testsuites"
    assert root.attrib["tests"] == "4"
    assert root.attrib["failures"] == "1"
    assert root.attrib["errors"] == "1"
    assert root.attrib["skipped"] == "1"

    suite = root.find("testsuite")
    assert suite is not None
    cases = suite.findall("testcase")
    assert len(cases) == 4

    failure = suite.find(".//failure")
    assert failure is not None
    assert "3 violating rows" in failure.attrib["message"]
    assert "SELECT 1" in (failure.text or "")

    error = suite.find(".//error")
    assert error is not None
    assert "relation does not exist" in error.attrib["message"]

    skipped = suite.find(".//skipped")
    assert skipped is not None
    assert "trigger" in skipped.attrib["message"]


def test_junit_is_valid_xml_with_quotes_and_newlines() -> None:
    report = make_report()
    report.results[0].sample = [{"note": 'line one\nline "two" & three'}]
    xml = JUnitRenderer().render(report)
    parsed = ET.fromstring(xml)
    failure = parsed.find(".//failure")
    assert failure is not None
    assert "line one" in (failure.text or "")
    assert "& three" in (failure.text or "")
