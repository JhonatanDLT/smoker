from __future__ import annotations

import os
import subprocess

import psycopg
import pytest

from pgproof.adapters.postgres.dialect import PostgresDialect
from pgproof.adapters.postgres.dump import DumpLoader
from pgproof.adapters.postgres.introspect import connect, introspect
from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.writer import PostgresRowWriter
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.application.generate import GenerateDataset
from pgproof.application.verify import VerifyDataset, VerifyRequest
from pgproof.core.exclusions import (
    build_exclusion_specs,
    next_value,
    parse_exclusion_definition,
    predicate_columns,
    predicate_matches,
    ranges_overlap,
)
from pgproof.core.filters import filter_catalog
from pgproof.core.generator import GenerationError, Generator
from pgproof.core.ir import (
    Column,
    ExclusionConstraint,
    ExclusionElement,
    Table,
    UniqueConstraint,
)
from pgproof.core.planner import build_plan

DSN = os.environ.get("PGPROOF_TEST_DSN")


def test_parse_exclusion_definition() -> None:
    method, elements, predicate = parse_exclusion_definition(
        "EXCLUDE USING gist (room WITH =, during WITH &&) WHERE (active)"
    )

    assert method == "gist"
    assert elements == [("room", "="), ("during", "&&")]
    assert predicate == "(active)"


def test_predicate_matches_simple_boolean_and_comparison() -> None:
    assert predicate_matches("(active)", {"active": True}) is True
    assert predicate_matches("(active)", {"active": False}) is False
    assert predicate_matches("(kind = 'paid')", {"kind": "paid"}) is True
    assert predicate_matches("(kind = 'paid')", {"kind": "open"}) is False
    assert predicate_matches(
        "(active AND kind = 'paid')", {"active": True, "kind": "paid"}
    ) is True
    assert predicate_matches(
        "(active AND kind = 'paid')", {"active": False, "kind": "paid"}
    ) is False


def test_predicate_matches_sqlserver_brackets() -> None:
    assert predicate_matches("([active]=(1))", {"active": True}) is True
    assert predicate_matches("([active]=(1))", {"active": False}) is False
    assert predicate_matches("(status = 'paid'::text)", {"status": "paid"}) is True
    assert predicate_columns("([active]=(1))") == {"active"}


def test_ranges_overlap() -> None:
    assert ranges_overlap("[1,10)", "[10,20)", "int4range") is False
    assert ranges_overlap("[1,11)", "[10,20)", "int4range") is True
    assert ranges_overlap(
        "[2025-01-01 10:00:00,2025-01-01 11:00:00)",
        "[2025-01-01 10:30:00,2025-01-01 11:30:00)",
        "tsrange",
    )
    assert not ranges_overlap(
        "[2025-01-01 10:00:00,2025-01-01 11:00:00)",
        "[2025-01-01 11:00:00,2025-01-01 12:00:00)",
        "tsrange",
    )


def test_exclusion_implied_by_unique_is_skipped() -> None:
    table = Table(
        schema="public",
        name="t",
        exclusions=[
            ExclusionConstraint(
                name="t_room_excl",
                elements=[ExclusionElement(column="room", operator="=")],
            )
        ],
        uniques=[UniqueConstraint(name="t_room_key", columns=["room"])],
    )
    table.columns.append(Column(name="room", sql_type="integer"))

    assert build_exclusion_specs(table) == []


def test_generator_makes_equality_columns_unique() -> None:
    catalog = DumpLoader().load(
        """
        CREATE TABLE booking (
            id integer PRIMARY KEY,
            room integer NOT NULL,
            CONSTRAINT room_excl EXCLUDE USING btree (room WITH =)
        );
        """
    )
    table = catalog.table("public.booking")
    generator = Generator(catalog, build_plan(catalog, rows=30, seed=7))
    rows = list(generator.generate_validated(table))

    rooms = [row[1] for row in rows]
    assert len(set(rooms)) == 30


def test_generator_ranges_do_not_overlap() -> None:
    catalog = DumpLoader().load(
        """
        CREATE TABLE booking (
            id integer PRIMARY KEY,
            during tsrange NOT NULL,
            CONSTRAINT during_excl EXCLUDE USING gist (during WITH &&)
        );
        """
    )
    table = catalog.table("public.booking")
    generator = Generator(catalog, build_plan(catalog, rows=30, seed=7))
    rows = list(generator.generate_validated(table))
    ranges = [row[1] for row in rows]

    assert all(
        not ranges_overlap(ranges[left], ranges[right], "tsrange")
        for left in range(len(ranges))
        for right in range(left + 1, len(ranges))
    )


def test_generator_respects_exclusion_predicate() -> None:
    catalog = DumpLoader().load(
        """
        CREATE TABLE booking (
            id integer PRIMARY KEY,
            room integer NOT NULL,
            active boolean NOT NULL,
            CONSTRAINT room_excl EXCLUDE USING btree (room WITH =) WHERE (active)
        );
        """
    )
    table = catalog.table("public.booking")
    generator = Generator(catalog, build_plan(catalog, rows=30, seed=11))
    rows = list(generator.generate_validated(table))
    active_rooms = [row[1] for row in rows if row[2]]

    assert len(set(active_rooms)) == len(active_rooms)


def test_unsupported_exclusion_operator_fails_loudly() -> None:
    catalog = DumpLoader().load(
        """
        CREATE TABLE booking (
            id integer PRIMARY KEY,
            during tsrange NOT NULL,
            CONSTRAINT during_excl EXCLUDE USING gist (during WITH -|-)
        );
        """
    )
    table = catalog.table("public.booking")
    generator = Generator(catalog, build_plan(catalog, rows=5, seed=7))

    with pytest.raises(GenerationError, match="cannot generate against"):
        list(generator.generate_validated(table))


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_exclusion_constraints_accept_generated_rows() -> None:
    assert DSN
    schema = "pgproof_excl_test"
    dump = f"""
    DROP SCHEMA IF EXISTS {schema} CASCADE;
    CREATE SCHEMA {schema};
    SET search_path TO {schema};
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    CREATE TABLE rooms (
        id integer PRIMARY KEY,
        room integer NOT NULL,
        CONSTRAINT room_excl EXCLUDE USING btree (room WITH =)
    );
    CREATE TABLE periods (
        id integer PRIMARY KEY,
        during tsrange NOT NULL,
        CONSTRAINT period_excl EXCLUDE USING gist (during WITH &&)
    );
    CREATE TABLE bookings (
        id integer PRIMARY KEY,
        room integer NOT NULL,
        during tsrange NOT NULL,
        active boolean NOT NULL,
        CONSTRAINT booking_excl EXCLUDE USING gist (room WITH =, during WITH &&)
            WHERE (active)
    );
    """
    subprocess.run(
        ["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", "-c", dump],
        check=True,
        capture_output=True,
    )
    catalog = DumpLoader().load(dump)

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=20, seed=13).execute()
    finally:
        writer.close()

    connection = psycopg.connect(DSN)
    try:
        report = VerifyDataset(
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), PostgresDialect(), seed=13
        ).execute(
            VerifyRequest(dataset_hash=generated.dataset_hash, plan=generated.plan)
        )
    finally:
        connection.close()

    assert report.verdict == "PASS"


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_live_introspection_reads_exclusion_constraints() -> None:
    assert DSN
    schema = "pgproof_excl_live"
    subprocess.run(
        [
            "psql",
            DSN,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f"""
            DROP SCHEMA IF EXISTS {schema} CASCADE;
            CREATE SCHEMA {schema};
            CREATE TABLE {schema}.booking (
                id integer PRIMARY KEY,
                room integer NOT NULL,
                during tsrange NOT NULL,
                active boolean NOT NULL DEFAULT true,
                CONSTRAINT no_overlap EXCLUDE USING gist
                    (room WITH =, during WITH &&) WHERE (active)
            );
            """,
        ],
        check=True,
        capture_output=True,
    )
    connection = connect(DSN)
    try:
        catalog = filter_catalog(introspect(connection), schemas=[schema])
    finally:
        connection.close()

    exclusion = catalog.table(f"{schema}.booking").exclusions[0]
    assert exclusion.name == "no_overlap"
    assert exclusion.method == "gist"
    assert [(element.column, element.operator) for element in exclusion.elements] == [
        ("room", "="),
        ("during", "&&"),
    ]
    assert exclusion.predicate == "(active)"


def test_next_value_advances_with_each_attempt() -> None:
    column = Column("seat", "integer", nullable=False)

    assert next_value(column, 4, 3, 0, 1) == 5
    assert next_value(column, 4, 3, 3, 1) == 8
    assert next_value(column, 4, 3, 0, 1) != next_value(column, 4, 3, 1, 1)
