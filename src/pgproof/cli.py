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
from .adapters.postgres.dump import load_dump
from .adapters.postgres.introspect import connect, introspect
from .adapters.postgres.profile import profile_catalog
from .adapters.postgres.query_runner import PostgresQueryRunner
from .adapters.postgres.sql_executor import PostgresSqlExecutor
from .adapters.postgres.writer import PostgresRowWriter
from .adapters.report import FileReportWriter, HtmlRenderer, JsonRenderer, JUnitRenderer
from .adapters.report.audit_markdown import AuditMarkdownRenderer
from .adapters.spec.yaml_loader import SpecError, YamlSpecLoader
from .application.audit import AuditSchema
from .application.generate import GenerateDataset
from .application.policy import FailPolicy, should_fail
from .application.repair import RepairDataset
from .application.run import RunPipeline
from .application.verify import VerifyDataset, VerifyRequest
from .core.errors import LoadError, RepairError
from .core.filters import filter_catalog, split_patterns
from .core.generator import GenerationError
from .core.invariants import semantic_for_catalog
from .core.ir import Catalog
from .core.planner import PlanError, build_plan
from .core.profile import Profile

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


def _apply_filters(
    catalog: Catalog,
    schema: str | None,
    include: str | None,
    exclude: str | None,
) -> Catalog:
    return filter_catalog(
        catalog,
        schemas=split_patterns(schema),
        include=split_patterns(include),
        exclude=split_patterns(exclude),
    )


def _introspect(
    dsn: str,
    schema: str | None = None,
    include: str | None = None,
    exclude: str | None = None,
) -> Catalog:
    with connect(dsn) as conn:
        catalog = introspect(conn)
    catalog = _apply_filters(catalog, schema, include, exclude)
    semantic_for_catalog(catalog)
    return catalog


def _load_catalog(
    path: Path | None,
    dsn: str | None,
    schema: str | None = None,
    include: str | None = None,
    exclude: str | None = None,
    schema_file: Path | None = None,
) -> Catalog:
    if path:
        catalog = Catalog.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        catalog = _apply_filters(catalog, schema, include, exclude)
        semantic_for_catalog(catalog)
        return catalog
    if schema_file:
        catalog = _apply_filters(load_dump(schema_file), schema, include, exclude)
        semantic_for_catalog(catalog)
        return catalog
    if dsn is None:
        raise typer.BadParameter("provide --dsn, --schema-file or --catalog")
    return _introspect(dsn, schema, include, exclude)


def _load_profile(path: Path | None) -> Profile | None:
    if path is None:
        return None
    return Profile.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


_REPORT_RENDERERS = {
    "json": JsonRenderer,
    "html": HtmlRenderer,
    "junit": JUnitRenderer,
}


def _report_writer(formats: str) -> FileReportWriter:
    renderers = []
    for name in formats.split(","):
        name = name.strip().lower()
        if not name:
            continue
        factory = _REPORT_RENDERERS.get(name)
        if factory is None:
            raise typer.BadParameter(
                f"unknown report format {name!r}; choose from "
                f"{', '.join(sorted(_REPORT_RENDERERS))}"
            )
        renderers.append(factory())
    if not renderers:
        raise typer.BadParameter("--format cannot be empty")
    return FileReportWriter(renderers)


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
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
    out: Path = typer.Option(Path("catalog.json"), "--out", "-o"),
) -> None:
    """Read a PostgreSQL catalog and save it as JSON."""
    catalog = _introspect(_dsn(dsn, dsn_env), schema, include, exclude)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(
        f"{len(catalog.tables)} tables · {sum(len(t.columns) for t in catalog.tables)} columns "
        f"→ {out}"
    )


@app.command("load-dump")
def load_dump_cmd(
    file: Path = typer.Argument(..., exists=True, dir_okay=False),
    out: Path = typer.Option(Path("catalog.json"), "--out", "-o"),
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
) -> None:
    """Load a schema dump (.sql) and save the catalog as JSON."""
    catalog = _apply_filters(load_dump(file), schema, include, exclude)
    semantic_for_catalog(catalog)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    typer.echo(
        f"{len(catalog.tables)} tables · "
        f"{sum(len(table.columns) for table in catalog.tables)} columns · "
        f"{len(catalog.functions)} functions → {out}"
    )


@app.command("plan")
def plan_cmd(
    dsn: str | None = typer.Option(None, "--dsn"),
    dsn_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--dsn-env"),
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", help="Load the catalog from a .sql dump"
    ),
    rows: int = typer.Option(1000, "--rows", help="Default rows per table"),
    seed: int = typer.Option(42, "--seed"),
    table_rows: str | None = typer.Option(None, "--table-rows"),
    out: Path = typer.Option(Path("plan.json"), "--out", "-o"),
) -> None:
    """Build and print a generation plan without touching any data."""
    catalog = _load_catalog(
        None,
        _dsn(dsn, dsn_env) if schema_file is None else None,
        schema,
        include,
        exclude,
        schema_file,
    )
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


@app.command("audit")
def audit_cmd(
    dsn: str | None = typer.Option(None, "--dsn", help="DSN or environment variable name"),
    dsn_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--dsn-env"),
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", help="Load the catalog from a .sql dump"
    ),
    out: Path = typer.Option(Path("audit.md"), "--out", "-o"),
) -> None:
    """Produce a schema readiness audit without reading any data."""
    catalog = _load_catalog(
        None,
        _dsn(dsn, dsn_env) if schema_file is None else None,
        schema,
        include,
        exclude,
        schema_file,
    )
    audit = AuditSchema(catalog).execute()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(AuditMarkdownRenderer().render(audit), encoding="utf-8")
    typer.echo(
        f"{audit.tables} tables · {audit.columns} columns · "
        f"{len(audit.risks)} risks · {audit.verdict} → {out}"
    )


@app.command("profile")
def profile_cmd(
    dsn: str | None = typer.Option(None, "--dsn", help="DSN or environment variable name"),
    dsn_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--dsn-env"),
    sample: int = typer.Option(1000, "--sample", help="Rows sampled per table"),
    out: Path = typer.Option(Path("profile.json"), "--out", "-o"),
    no_values: bool = typer.Option(
        False, "--no-values", help="Omit top values for PII-sensitive schemas"
    ),
    sample_method: str = typer.Option(
        "auto",
        "--sample-method",
        help="auto, limit, system or bernoulli",
    ),
    sample_seed: int = typer.Option(42, "--sample-seed"),
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
) -> None:
    """Sample the source and save a profile for distribution-aware generation."""
    if sample_method not in {"auto", "limit", "system", "bernoulli"}:
        _fail(
            "invalid --sample-method: choose auto, limit, system or bernoulli"
        )
        return
    dsn_value = _dsn(dsn, dsn_env)
    catalog = _introspect(dsn_value, schema, include, exclude)
    with connect(dsn_value) as connection:
        profile = profile_catalog(
            connection,
            catalog.tables,
            sample=sample,
            method=sample_method,
            seed=sample_seed,
        )
    if no_values:
        for table in profile.tables.values():
            for column_stats in table.columns.values():
                column_stats.top_values = []
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    typer.echo(
        f"{len(profile.tables)} tables · sample {sample} · "
        f"{sum(len(t.columns) for t in profile.tables.values())} columns → {out}"
    )


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
    profile_path: Path | None = typer.Option(None, "--profile", help="Profile JSON"),
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", help="Load the catalog from a .sql dump"
    ),
) -> None:
    """Generate rows into the target database."""
    source_dsn = _dsn(source, source_env) if schema_file is None else None
    target_dsn = _dsn(target, target_env)
    if source_dsn is not None and source_dsn == target_dsn:
        typer.echo("warning: source and target are the same database", err=True)

    catalog = _load_catalog(
        catalog_path, source_dsn, schema, include, exclude, schema_file
    )
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
        profile=_load_profile(profile_path),
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
    format: str = typer.Option(
        "json,html,junit", "--format", help="Comma-separated: json,html,junit"
    ),
    fail_on: FailPolicy = typer.Option(FailPolicy.FAIL, "--fail-on"),
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", help="Load the catalog from a .sql dump"
    ),
) -> None:
    """Verify every invariant against the target database."""
    target_dsn = _dsn(dsn, dsn_env)
    connection = psycopg.connect(target_dsn)
    try:
        catalog = _load_catalog(
            catalog_path, target_dsn, schema, include, exclude, schema_file
        )
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

    paths = _report_writer(format).write(report, str(out))
    _print_summary(report)
    for path in paths:
        typer.echo(f"report: {path}")
    if should_fail(report, fail_on):
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
    format: str = typer.Option(
        "json,html,junit", "--format", help="Comma-separated: json,html,junit"
    ),
    fail_on: FailPolicy = typer.Option(FailPolicy.FAIL, "--fail-on"),
    max_passes: int = typer.Option(3, "--max-passes", help="Repair fixed-point limit"),
    profile_path: Path | None = typer.Option(None, "--profile", help="Profile JSON"),
    schema: str | None = typer.Option(None, "--schema", help="Comma-separated schemas"),
    include: str | None = typer.Option(None, "--include", help="Comma-separated globs"),
    exclude: str | None = typer.Option(None, "--exclude", help="Comma-separated globs"),
    schema_file: Path | None = typer.Option(
        None, "--schema-file", help="Load the catalog from a .sql dump"
    ),
) -> None:
    """Introspect, generate, verify and write a report."""
    started = time.perf_counter()
    source_dsn = _dsn(source, source_env) if schema_file is None else None
    target_dsn = _dsn(target, target_env)
    if source_dsn is not None and source_dsn == target_dsn:
        typer.echo("warning: source and target are the same database", err=True)

    catalog = _load_catalog(
        catalog_path, source_dsn, schema, include, exclude, schema_file
    )
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
        profile=_load_profile(profile_path),
    )
    verify = VerifyDataset(catalog, PostgresQueryRunner(connection), YamlSpecLoader(), seed=seed)
    repair = RepairDataset(
        PostgresQueryRunner(connection),
        PostgresSqlExecutor(connection),
        max_passes=max_passes,
    )

    try:
        result = RunPipeline(generate, verify, repair).execute(
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
    except RepairError as exc:
        _fail(str(exc))
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

    paths = _report_writer(format).write(result.report, str(out))
    if result.repairs.actions:
        typer.echo(
            f"repairs: {result.repairs.applied} rows across "
            f"{len(result.repairs.actions)} actions in {result.repairs.passes} pass(es), "
            f"residual {result.repairs.residual}"
        )
    _print_summary(result.report)
    typer.echo(f"elapsed: {time.perf_counter() - started:.2f}s")
    for path in paths:
        typer.echo(f"report: {path}")
    if should_fail(result.report, fail_on):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
