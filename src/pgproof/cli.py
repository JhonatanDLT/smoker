"""pgproof command line interface.

This is the composition root: it parses arguments, wires concrete adapters to
the use cases, and formats output. No business logic lives here.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import psycopg
import typer

from . import __version__
from .adapters.postgres.introspect import connect, introspect
from .adapters.postgres.query_runner import PostgresQueryRunner
from .adapters.postgres.writer import PostgresRowWriter
from .adapters.report import FileReportWriter, HtmlRenderer, JsonRenderer
from .adapters.spec.yaml_loader import SpecError, YamlSpecLoader
from .application.generate import GenerateDataset
from .application.run import RunPipeline
from .application.verify import VerifyDataset, VerifyRequest
from .core.errors import LoadError
from .core.generator import GenerationError
from .core.invariants import semantic_for_catalog
from .core.ir import Catalog
from .core.planner import PlanError, build_plan

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Synthetic Postgres data that ships with proof.",
)


# --------------------------------------------------------------------- helpers
def _dsn(value: str | None, env_name: str) -> str:
    """Accept a DSN, or the name of an environment variable holding one."""
    if value:
        if "://" in value or "=" in value:
            return value
        resolved = os.environ.get(value)
        if resolved:
            return resolved
        raise typer.BadParameter(
            f"{value!r} is neither a DSN nor the name of a set environment variable"
        )
    resolved = os.environ.get(env_name)
    if not resolved:
        raise typer.BadParameter(f"pass --dsn or set {env_name}")
    return resolved


def _parse_table_rows(raw: str | None) -> dict[str, int]:
    result: dict[str, int] = {}
    if not raw:
        return result
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise typer.BadParameter("--table-rows expects table=rows pairs, e.g. users=1000")
        key, value = chunk.split("=", 1)
        try:
            result[key.strip()] = int(value)
        except ValueError as exc:
            raise typer.BadParameter(f"invalid row count in {chunk!r}") from exc
    return result


def _introspect(dsn: str) -> Catalog:
    with connect(dsn) as conn:
        catalog = introspect(conn)
    semantic_for_catalog(catalog)
    return catalog


def _load_catalog(path: Path | None, dsn: str) -> Catalog:
    if path:
        catalog = Catalog.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    else:
        catalog = _introspect(dsn)
    semantic_for_catalog(catalog)
    return catalog


def _report_writer() -> FileReportWriter:
    return FileReportWriter([JsonRenderer(), HtmlRenderer()])


def _command_line() -> str:
    return "pgproof " + " ".join(sys.argv[1:])


def _print_summary(report) -> None:
    summary = report.summary()
    typer.echo("")
    typer.echo(
        f"RESULT {report.verdict} · {summary['passed']} passed · "
        f"{summary['failed']} failed · {summary['error']} errors · "
        f"{summary['skipped']} skipped · {summary['violations']} violations"
    )
    for result in report.results:
        if result.status in {"fail", "error"}:
            suffix = f" ({result.error})" if result.error else ""
            typer.echo(
                f"  {result.status.upper():<6} {result.id} · {result.violations} rows{suffix}"
            )


def _fail(message: str, code: int = 2) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _progress(key: str, rows: int, elapsed: float) -> None:
    typer.echo(f"  generate {key:<36} {rows:>9,} rows  {elapsed:6.2f}s")


# -------------------------------------------------------------------- commands
@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"pgproof {__version__}")


@app.command("introspect")
def introspect_cmd(
    dsn: str | None = typer.Option(None, "--dsn", help="DSN or environment variable name"),
    dsn_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--dsn-env"),
    out: Path = typer.Option(Path("catalog.json"), "--out", "-o"),
) -> None:
    """Read a PostgreSQL catalog and save it as JSON."""
    catalog = _introspect(_dsn(dsn, dsn_env))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(
        f"{len(catalog.tables)} tables · {sum(len(t.columns) for t in catalog.tables)} columns "
        f"→ {out}"
    )


@app.command("plan")
def plan_cmd(
    dsn: str | None = typer.Option(None, "--dsn"),
    dsn_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--dsn-env"),
    rows: int = typer.Option(1000, "--rows", help="Default rows per table"),
    seed: int = typer.Option(42, "--seed"),
    table_rows: str | None = typer.Option(None, "--table-rows"),
    out: Path = typer.Option(Path("plan.json"), "--out", "-o"),
) -> None:
    """Build and print a generation plan without touching any data."""
    catalog = _introspect(_dsn(dsn, dsn_env))
    try:
        plan = build_plan(
            catalog, rows=rows, table_rows=_parse_table_rows(table_rows), seed=seed
        )
    except PlanError as exc:
        _fail(f"plan error: {exc}")
        return
    payload = {
        "seed": plan.seed,
        "total_rows": plan.total_rows,
        "order": plan.order,
        "cycles": plan.cycles,
        "warnings": plan.warnings,
        "tables": {
            key: {"rows": value.rows, "parents": value.parents}
            for key, value in plan.tables.items()
        },
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for warning in plan.warnings:
        typer.echo(f"warn: {warning}", err=True)
    typer.echo(f"{len(plan.tables)} tables · {plan.total_rows:,} rows → {out}")


@app.command("generate")
def generate_cmd(
    source: str | None = typer.Option(None, "--source", help="DSN or env var name"),
    source_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--source-env"),
    target: str | None = typer.Option(None, "--target", help="DSN or env var name"),
    target_env: str = typer.Option("PGPROOF_TARGET_DSN", "--target-env"),
    rows: int = typer.Option(1000, "--rows"),
    seed: int = typer.Option(42, "--seed"),
    table_rows: str | None = typer.Option(None, "--table-rows"),
    truncate_first: bool = typer.Option(True, "--truncate/--no-truncate"),
    method: str = typer.Option("copy", "--method", help="copy or insert"),
    catalog_path: Path | None = typer.Option(None, "--catalog"),
) -> None:
    """Generate rows into the target database."""
    source_dsn = _dsn(source, source_env)
    target_dsn = _dsn(target, target_env)
    if source_dsn == target_dsn:
        typer.echo("warning: source and target are the same database", err=True)

    catalog = _load_catalog(catalog_path, source_dsn)
    connection = psycopg.connect(target_dsn)
    writer = PostgresRowWriter(connection)
    use_case = GenerateDataset(
        catalog,
        writer,
        rows=rows,
        table_rows=_parse_table_rows(table_rows),
        seed=seed,
        method=method,
        truncate=truncate_first,
        progress=_progress,
    )
    try:
        result = use_case.execute()
    except LoadError as exc:
        _fail(exc.format())
        return
    except GenerationError as exc:
        _fail(f"generation failed: {exc}")
        return
    finally:
        writer.close()

    for warning in result.plan.warnings:
        typer.echo(f"warn: {warning}", err=True)
    typer.echo(f"generated {sum(result.counts.values()):,} rows with seed {seed}")


@app.command("verify")
def verify_cmd(
    dsn: str | None = typer.Option(None, "--dsn", help="DSN or environment variable name"),
    dsn_env: str = typer.Option("PGPROOF_TARGET_DSN", "--dsn-env"),
    spec: Path | None = typer.Option(None, "--spec", help="YAML file with app-level rules"),
    out: Path = typer.Option(Path("proof"), "--out", "-o"),
    sample: int = typer.Option(5, "--sample", help="Rows to show per failure"),
    seed: int = typer.Option(42, "--seed"),
    catalog_path: Path | None = typer.Option(None, "--catalog"),
) -> None:
    """Verify every invariant against the target database."""
    target_dsn = _dsn(dsn, dsn_env)
    connection = psycopg.connect(target_dsn)
    try:
        catalog = _load_catalog(catalog_path, target_dsn)
        use_case = VerifyDataset(
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), seed=seed
        )
        report = use_case.execute(
            VerifyRequest(
                spec_path=str(spec) if spec else None,
                sample=sample,
                command=_command_line(),
                dataset_hash="n/a (verification only)",
            )
        )
    except SpecError as exc:
        _fail(f"invalid spec: {exc}")
        return
    finally:
        connection.close()

    paths = _report_writer().write(report, str(out))
    _print_summary(report)
    for path in paths:
        typer.echo(f"report: {path}")
    if report.verdict != "PASS":
        raise typer.Exit(code=1)


@app.command("run")
def run_cmd(
    source: str | None = typer.Option(None, "--source", help="DSN or env var name"),
    source_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--source-env"),
    target: str | None = typer.Option(None, "--target", help="DSN or env var name"),
    target_env: str = typer.Option("PGPROOF_TARGET_DSN", "--target-env"),
    rows: int = typer.Option(1000, "--rows"),
    seed: int = typer.Option(42, "--seed"),
    table_rows: str | None = typer.Option(None, "--table-rows"),
    spec: Path | None = typer.Option(None, "--spec", help="YAML file with app-level rules"),
    out: Path = typer.Option(Path("proof"), "--out", "-o"),
    truncate_first: bool = typer.Option(True, "--truncate/--no-truncate"),
    method: str = typer.Option("copy", "--method", help="copy or insert"),
    sample: int = typer.Option(5, "--sample"),
    catalog_path: Path | None = typer.Option(None, "--catalog"),
) -> None:
    """Introspect, generate, verify and write a report."""
    started = time.perf_counter()
    source_dsn = _dsn(source, source_env)
    target_dsn = _dsn(target, target_env)
    if source_dsn == target_dsn:
        typer.echo("warning: source and target are the same database", err=True)

    catalog = _load_catalog(catalog_path, source_dsn)
    out.mkdir(parents=True, exist_ok=True)
    (out / "catalog.json").write_text(
        json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    connection = psycopg.connect(target_dsn)
    writer = PostgresRowWriter(connection)
    generate = GenerateDataset(
        catalog,
        writer,
        rows=rows,
        table_rows=_parse_table_rows(table_rows),
        seed=seed,
        method=method,
        truncate=truncate_first,
        progress=_progress,
    )
    verify = VerifyDataset(catalog, PostgresQueryRunner(connection), YamlSpecLoader(), seed=seed)

    try:
        result = RunPipeline(generate, verify).execute(
            VerifyRequest(
                spec_path=str(spec) if spec else None,
                sample=sample,
                command=_command_line(),
            )
        )
    except LoadError as exc:
        _fail(exc.format())
        return
    except GenerationError as exc:
        _fail(f"generation failed: {exc}")
        return
    except SpecError as exc:
        _fail(f"invalid spec: {exc}")
        return
    finally:
        connection.close()

    (out / "plan.json").write_text(
        json.dumps(
            {
                "seed": result.generated.plan.seed,
                "total_rows": result.generated.plan.total_rows,
                "order": result.generated.plan.order,
                "cycles": result.generated.plan.cycles,
                "warnings": result.generated.plan.warnings,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for warning in result.generated.plan.warnings:
        typer.echo(f"warn: {warning}", err=True)

    paths = _report_writer().write(result.report, str(out))
    _print_summary(result.report)
    typer.echo(f"elapsed: {time.perf_counter() - started:.2f}s")
    for path in paths:
        typer.echo(f"report: {path}")
    if result.report.verdict != "PASS":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
