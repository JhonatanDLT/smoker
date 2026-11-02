"""pgproof command line interface."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import psycopg
import typer

from . import __version__
from .adapters.postgres.introspect import connect, introspect, table_counts
from .adapters.postgres.writer import reset_sequences, truncate, write_table
from .core.generator import GenerationError, Generator
from .core.invariants import (
    invariants_from_catalog,
    invariants_from_spec,
    merge_invariants,
    semantic_for_catalog,
)
from .core.ir import Catalog
from .core.planner import PlanError, build_plan
from .core.report import build_report, write_report
from .core.verifier import structural_warnings, verify_all

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Synthetic Postgres data that ships with proof.",
)


# --------------------------------------------------------------------- helpers
def _dsn(value: Optional[str], env_name: str) -> str:
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


def _parse_table_rows(raw: Optional[str]) -> dict[str, int]:
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


def _load_catalog(path: Optional[Path], dsn: str) -> Catalog:
    if path:
        catalog = Catalog.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    else:
        catalog = _introspect(dsn)
    semantic_for_catalog(catalog)
    return catalog


def _load_spec(path: Optional[Path]) -> list:
    if not path:
        return []
    return invariants_from_spec(path)


# -------------------------------------------------------------------- commands
@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"pgproof {__version__}")


@app.command("introspect")
def introspect_cmd(
    dsn: Optional[str] = typer.Option(None, "--dsn", help="DSN or environment variable name"),
    dsn_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--dsn-env"),
    out: Path = typer.Option(Path("catalog.json"), "--out", "-o"),
) -> None:
    """Read a PostgreSQL catalog and save it as JSON."""
    catalog = _introspect(_dsn(dsn, dsn_env))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    typer.echo(
        f"{len(catalog.tables)} tables · {sum(len(t.columns) for t in catalog.tables)} columns "
        f"→ {out}"
    )


@app.command("plan")
def plan_cmd(
    dsn: Optional[str] = typer.Option(None, "--dsn"),
    dsn_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--dsn-env"),
    rows: int = typer.Option(1000, "--rows", help="Default rows per table"),
    seed: int = typer.Option(42, "--seed"),
    table_rows: Optional[str] = typer.Option(None, "--table-rows"),
    out: Path = typer.Option(Path("plan.json"), "--out", "-o"),
) -> None:
    """Build and print a generation plan without touching any data."""
    catalog = _introspect(_dsn(dsn, dsn_env))
    plan = build_plan(catalog, rows=rows, table_rows=_parse_table_rows(table_rows), seed=seed)
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
    source: Optional[str] = typer.Option(None, "--source", help="DSN or env var name"),
    source_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--source-env"),
    target: Optional[str] = typer.Option(None, "--target", help="DSN or env var name"),
    target_env: str = typer.Option("PGPROOF_TARGET_DSN", "--target-env"),
    rows: int = typer.Option(1000, "--rows"),
    seed: int = typer.Option(42, "--seed"),
    table_rows: Optional[str] = typer.Option(None, "--table-rows"),
    truncate_first: bool = typer.Option(True, "--truncate/--no-truncate"),
    method: str = typer.Option("copy", "--method", help="copy or insert"),
    catalog_path: Optional[Path] = typer.Option(None, "--catalog"),
) -> None:
    """Generate rows into the target database."""
    source_dsn = _dsn(source, source_env)
    target_dsn = _dsn(target, target_env)
    if source_dsn == target_dsn:
        typer.echo("warning: source and target are the same database", err=True)

    catalog = _load_catalog(catalog_path, source_dsn)
    plan = build_plan(catalog, rows=rows, table_rows=_parse_table_rows(table_rows), seed=seed)
    for warning in plan.warnings:
        typer.echo(f"warn: {warning}", err=True)

    generator = Generator(catalog, plan)
    connection = psycopg.connect(target_dsn)
    try:
        if truncate_first:
            truncate(connection, catalog.tables, cascade=True)
        for key in plan.order:
            table = catalog.table(key)
            started = time.perf_counter()
            written = write_table(
                connection, table, generator.generate_validated(table), method=method
            )
            elapsed = time.perf_counter() - started
            typer.echo(f"  {key:<42} {written:>9,} rows  {elapsed:6.2f}s")
        reset_sequences(connection, catalog.tables)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    typer.echo(f"generated {plan.total_rows:,} rows with seed {seed}")


@app.command("verify")
def verify_cmd(
    dsn: Optional[str] = typer.Option(None, "--dsn", help="DSN or environment variable name"),
    dsn_env: str = typer.Option("PGPROOF_TARGET_DSN", "--dsn-env"),
    spec: Optional[Path] = typer.Option(None, "--spec", help="YAML file with app-level rules"),
    out: Path = typer.Option(Path("proof"), "--out", "-o"),
    sample: int = typer.Option(5, "--sample", help="Rows to show per failure"),
    seed: int = typer.Option(42, "--seed"),
    catalog_path: Optional[Path] = typer.Option(None, "--catalog"),
) -> None:
    """Verify every invariant against the target database."""
    target_dsn = _dsn(dsn, dsn_env)
    connection = psycopg.connect(target_dsn)
    try:
        catalog = _load_catalog(catalog_path, target_dsn)
        invariants = merge_invariants(invariants_from_catalog(catalog), _load_spec(spec))
        verification = verify_all(connection, invariants, sample_limit=sample)
        counts = table_counts(connection, catalog.table_keys)
        warnings = structural_warnings(connection)
        command = "pgproof " + " ".join(sys.argv[1:])
        empty_plan = build_plan(catalog, rows=0, seed=seed)
        report = build_report(catalog, verification, empty_plan, counts, warnings, command)
        report.dataset_hash = "n/a (verification only)"
        json_path, html_path = write_report(report, out)
    finally:
        connection.close()

    _print_summary(report)
    typer.echo(f"report: {json_path}")
    typer.echo(f"html:   {html_path}")
    if report.verdict != "PASS":
        raise typer.Exit(code=1)


@app.command("run")
def run_cmd(
    source: Optional[str] = typer.Option(None, "--source", help="DSN or env var name"),
    source_env: str = typer.Option("PGPROOF_SOURCE_DSN", "--source-env"),
    target: Optional[str] = typer.Option(None, "--target", help="DSN or env var name"),
    target_env: str = typer.Option("PGPROOF_TARGET_DSN", "--target-env"),
    rows: int = typer.Option(1000, "--rows"),
    seed: int = typer.Option(42, "--seed"),
    table_rows: Optional[str] = typer.Option(None, "--table-rows"),
    spec: Optional[Path] = typer.Option(None, "--spec", help="YAML file with app-level rules"),
    out: Path = typer.Option(Path("proof"), "--out", "-o"),
    truncate_first: bool = typer.Option(True, "--truncate/--no-truncate"),
    method: str = typer.Option("copy", "--method", help="copy or insert"),
    sample: int = typer.Option(5, "--sample"),
    catalog_path: Optional[Path] = typer.Option(None, "--catalog"),
) -> None:
    """Introspect, generate, verify and write a report."""
    started = time.perf_counter()
    source_dsn = _dsn(source, source_env)
    target_dsn = _dsn(target, target_env)
    if source_dsn == target_dsn:
        typer.echo("warning: source and target are the same database", err=True)

    out.mkdir(parents=True, exist_ok=True)
    catalog = _load_catalog(catalog_path, source_dsn)
    (out / "catalog.json").write_text(
        json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    plan = build_plan(catalog, rows=rows, table_rows=_parse_table_rows(table_rows), seed=seed)
    (out / "plan.json").write_text(
        json.dumps(
            {
                "seed": plan.seed,
                "total_rows": plan.total_rows,
                "order": plan.order,
                "cycles": plan.cycles,
                "warnings": plan.warnings,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for warning in plan.warnings:
        typer.echo(f"warn: {warning}", err=True)

    typer.echo(
        f"pgproof {__version__} · {len(catalog.tables)} tables · "
        f"{plan.total_rows:,} rows · seed {seed}"
    )

    generator = Generator(catalog, plan)
    hasher = hashlib.sha256()
    connection = psycopg.connect(target_dsn)
    try:
        if truncate_first:
            truncate(connection, catalog.tables, cascade=True)
        for key in plan.order:
            table = catalog.table(key)
            table_started = time.perf_counter()
            written = write_table(
                connection,
                table,
                generator.generate_validated(table),
                method=method,
                hasher=hasher,
            )
            typer.echo(
                f"  generate {key:<36} {written:>9,} rows  "
                f"{time.perf_counter() - table_started:6.2f}s"
            )
        reset_sequences(connection, catalog.tables)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    connection = psycopg.connect(target_dsn)
    try:
        invariants = merge_invariants(invariants_from_catalog(catalog), _load_spec(spec))
        verification = verify_all(connection, invariants, sample_limit=sample)
        counts = table_counts(connection, catalog.table_keys)
        warnings = list(plan.warnings) + structural_warnings(connection)
        command = "pgproof " + " ".join(sys.argv[1:])
        report = build_report(
            catalog,
            verification,
            plan,
            counts,
            warnings,
            command,
            dataset_hash=hasher.hexdigest(),
        )
        json_path, html_path = write_report(report, out)
    finally:
        connection.close()

    _print_summary(report)
    typer.echo(f"elapsed: {time.perf_counter() - started:.2f}s")
    typer.echo(f"report: {json_path}")
    typer.echo(f"html:   {html_path}")
    if report.verdict != "PASS":
        raise typer.Exit(code=1)


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
            typer.echo(f"  {result.status.upper():<6} {result.id} · {result.violations} rows{suffix}")


if __name__ == "__main__":
    app()
