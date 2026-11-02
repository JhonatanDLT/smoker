"""Architecture guards.

These tests fail the build if the dependency rule is broken:

    cli -> application -> core <- adapters

The core and the application layer must not import infrastructure (psycopg,
typer, yaml) or outer layers (adapters, cli).
"""

from __future__ import annotations

import ast
from pathlib import Path

from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.sql_executor import PostgresSqlExecutor
from pgproof.adapters.postgres.writer import PostgresRowWriter
from pgproof.adapters.report import FileReportWriter, HtmlRenderer, JsonRenderer
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.core.ports import (
    QueryRunner,
    ReportRenderer,
    ReportWriter,
    RowWriter,
    SpecLoader,
    SqlExecutor,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "pgproof"
CORE = SRC / "core"
APPLICATION = SRC / "application"

INFRASTRUCTURE = {"psycopg", "typer", "yaml", "fastapi", "flask", "requests", "click"}
OUTER_LAYERS = {"adapters", "application", "cli"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _top(name: str) -> str:
    return name.split(".")[0]


def test_core_does_not_depend_on_infrastructure_or_outer_layers() -> None:
    for path in CORE.rglob("*.py"):
        for name in _imports(path):
            assert _top(name) not in INFRASTRUCTURE, f"{path} imports {name}"
            assert _top(name) not in OUTER_LAYERS, f"{path} imports {name}"


def test_application_does_not_depend_on_adapters_or_frameworks() -> None:
    for path in APPLICATION.rglob("*.py"):
        for name in _imports(path):
            assert _top(name) not in INFRASTRUCTURE, f"{path} imports {name}"
            assert _top(name) != "adapters", f"{path} imports {name}"


def test_adapters_satisfy_their_ports() -> None:
    assert isinstance(PostgresRowWriter.__new__(PostgresRowWriter), RowWriter)
    assert isinstance(PostgresQueryRunner.__new__(PostgresQueryRunner), QueryRunner)
    assert isinstance(PostgresSqlExecutor.__new__(PostgresSqlExecutor), SqlExecutor)
    assert isinstance(YamlSpecLoader(), SpecLoader)
    assert isinstance(JsonRenderer(), ReportRenderer)
    assert isinstance(HtmlRenderer(), ReportRenderer)
    assert isinstance(FileReportWriter([]), ReportWriter)
