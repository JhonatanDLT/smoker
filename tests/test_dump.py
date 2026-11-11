from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pgproof.adapters.postgres.dialect import PostgresDialect
from pgproof.adapters.postgres.dump import DumpLoader, load_dump, split_statements
from pgproof.adapters.postgres.introspect import connect, introspect
from pgproof.adapters.postgres.query_runner import PostgresQueryRunner
from pgproof.adapters.postgres.writer import PostgresRowWriter
from pgproof.adapters.spec.yaml_loader import YamlSpecLoader
from pgproof.application.generate import GenerateDataset
from pgproof.application.verify import VerifyDataset, VerifyRequest
from pgproof.core.filters import filter_catalog
from pgproof.core.invariants import InvariantKind, invariants_from_catalog

DSN = os.environ.get("PGPROOF_TEST_DSN")
ROOT = Path(__file__).resolve().parents[1]


def test_split_statements_handles_dollar_quotes_and_copy() -> None:
    sql = """
-- a comment
CREATE TABLE t (id int);
CREATE FUNCTION f() RETURNS trigger AS $$
BEGIN
  -- a semicolon; inside the body
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
COPY t (id) FROM stdin;
1
2
\\.
ALTER TABLE t ADD CONSTRAINT t_pkey PRIMARY KEY (id);
"""
    statements = split_statements(sql)
    assert len(statements) == 4
    assert statements[0].startswith("CREATE TABLE")
    assert statements[1].startswith("CREATE FUNCTION")
    assert statements[2].startswith("COPY")
    assert statements[3].startswith("ALTER TABLE")


def test_billing_dump_structure() -> None:
    catalog = load_dump(ROOT / "examples/saas-billing/schema.sql")

    assert len(catalog.tables) == 10
    assert len(catalog.functions) == 1

    users = catalog.table("billing.users")
    assert users.primary_key == ["id"]
    assert users.foreign_keys[0].parent_table == "accounts"
    assert users.uniques[0].columns == ["email"]

    accounts = catalog.table("billing.accounts")
    assert accounts.triggers[0].function == "touch_updated_at"
    assert accounts.foreign_keys[0].parent_table == "accounts"

    plans = catalog.table("billing.plans")
    assert plans.column("tier").enum_values == ["free", "pro", "business"]

    subscriptions = catalog.table("billing.subscriptions")
    assert subscriptions.column("status").enum_values == [
        "trialing",
        "active",
        "past_due",
        "canceled",
    ]


def test_partitioned_dump_tree() -> None:
    catalog = load_dump(ROOT / "examples/partitioned-events/schema.sql")

    logs = catalog.table("part.logs")
    assert logs.is_partitioned
    assert logs.has_sub_partitions
    assert len(logs.partitions) == 2
    assert logs.partitions[0].children
    assert logs.partitions[0].lower == [date(2025, 1, 1)]

    tenant_events = catalog.table("part.tenant_events")
    assert tenant_events.partition_key == ["tenant_id", "created_at"]
    assert tenant_events.partitions[0].lower == [1, None]
    assert tenant_events.partitions[1].upper == [200, None]


def test_inferred_foreign_key_from_naming() -> None:
    dump = """
    CREATE TABLE customers (id integer PRIMARY KEY);
    CREATE TABLE orders (
        id integer PRIMARY KEY,
        customer_id integer NOT NULL
    );
    """
    catalog = DumpLoader().load(dump)
    orders = catalog.table("public.orders")

    assert len(orders.foreign_keys) == 1
    foreign_key = orders.foreign_keys[0]
    assert foreign_key.inferred is True
    assert foreign_key.parent_table == "customers"
    assert foreign_key.child_columns == ["customer_id"]
    assert foreign_key.provenance is not None
    assert foreign_key.provenance.kind == "inferred"


def test_like_and_inherits() -> None:
    dump = """
    CREATE TABLE base (
        id integer PRIMARY KEY,
        name text,
        created_at timestamptz DEFAULT now(),
        CONSTRAINT base_name_check CHECK (length(name) > 0)
    );
    CREATE TABLE child (LIKE base INCLUDING ALL);
    CREATE TABLE inherited (extra integer) INHERITS (base);
    """
    catalog = DumpLoader().load(dump)

    child = catalog.table("public.child")
    assert [column.name for column in child.columns] == ["id", "name", "created_at"]
    assert child.primary_key == ["id"]
    assert child.column("created_at").default is not None
    assert len(child.checks) == 1

    inherited = catalog.table("public.inherited")
    assert [column.name for column in inherited.columns] == [
        "extra",
        "id",
        "name",
        "created_at",
    ]
    assert inherited.primary_key == []
    assert len(inherited.checks) == 1


def test_attach_partition() -> None:
    dump = """
    CREATE TABLE parent (
        id integer,
        created_at date,
        PRIMARY KEY (id, created_at)
    ) PARTITION BY RANGE (created_at);
    CREATE TABLE standalone (
        id integer,
        created_at date,
        PRIMARY KEY (id, created_at)
    );
    ALTER TABLE parent ATTACH PARTITION standalone
        FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
    """
    catalog = DumpLoader().load(dump)

    assert [table.key for table in catalog.tables] == ["public.parent"]
    parent = catalog.table("public.parent")
    assert len(parent.partitions) == 1
    assert parent.partitions[0].name == "standalone"
    assert parent.partitions[0].lower == [date(2025, 1, 1)]


def test_cte_join_inference() -> None:
    dump = """
    CREATE TABLE customers (id integer PRIMARY KEY);
    CREATE TABLE orders (id integer PRIMARY KEY, customer_id integer NOT NULL);
    CREATE VIEW v AS
    WITH x AS (
        SELECT c.id, o.customer_id
        FROM customers c
        JOIN orders o ON o.customer_id = c.id
    )
    SELECT * FROM x;
    """
    catalog = DumpLoader().load(dump)
    orders = catalog.table("public.orders")

    assert any(
        foreign_key.parent_table == "customers" and foreign_key.inferred
        for foreign_key in orders.foreign_keys
    )


def test_function_dynamic_sql_conditions_and_writes() -> None:
    dump = """
    CREATE TABLE events (id integer PRIMARY KEY, amount numeric);
    CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.amount < 0 THEN
            RAISE EXCEPTION 'negative';
        END IF;
        EXECUTE 'UPDATE events SET amount = 1 WHERE id = 2';
    END;
    $$;
    """
    catalog = DumpLoader().load(dump)
    function = catalog.functions[0]

    assert function.language == "plpgsql"
    assert any("NEW.amount" in condition for condition in function.conditions)
    assert any("UPDATE events" in statement for statement in function.statements)
    assert "events" in function.writes
    assert function.provenance is not None


def test_trigger_when_and_update_of() -> None:
    dump = """
    CREATE TABLE t (id integer, updated_at timestamptz);
    CREATE FUNCTION touch() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RETURN NEW; END;
    $$;
    CREATE TRIGGER t_touch BEFORE UPDATE OF updated_at ON t
        FOR EACH ROW WHEN (OLD.* IS DISTINCT FROM NEW.*)
        EXECUTE FUNCTION touch();
    """
    catalog = DumpLoader().load(dump)
    trigger = catalog.table("public.t").triggers[0]

    assert trigger.update_of == ["updated_at"]
    assert "OLD.*" in (trigger.when_clause or "")
    assert trigger.row_level is True
    assert trigger.provenance is not None


def test_raw_objects_are_stored() -> None:
    dump = """
    CREATE VIEW v AS SELECT 1;
    CREATE RULE r AS ON INSERT TO t DO INSTEAD NOTHING;
    CREATE POLICY p ON t USING (true);
    CREATE SEQUENCE s;
    CREATE EXTENSION pgcrypto;
    """
    catalog = DumpLoader().load(dump)
    kinds = {obj.kind for obj in catalog.objects}

    assert {"view", "rule", "policy", "sequence", "extension"} <= kinds
    assert all(obj.provenance is not None for obj in catalog.objects)


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_dump_matches_live_introspection() -> None:
    assert DSN
    subprocess.run(
        [
            "psql",
            DSN,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(ROOT / "examples/saas-billing/schema.sql"),
        ],
        check=True,
        capture_output=True,
    )
    dump = load_dump(ROOT / "examples/saas-billing/schema.sql")
    with connect(DSN) as conn:
        live = filter_catalog(introspect(conn), schemas=["billing"])

    dump_tables = {table.key: table for table in dump.tables}
    live_tables = {table.key: table for table in live.tables}
    assert set(dump_tables) == set(live_tables)

    for key, dump_table in dump_tables.items():
        live_table = live_tables[key]
        assert [column.name for column in dump_table.columns] == [
            column.name for column in live_table.columns
        ]
        assert dump_table.primary_key == live_table.primary_key
        assert len(dump_table.foreign_keys) == len(live_table.foreign_keys)
        assert len(dump_table.checks) == len(live_table.checks)
        assert len(dump_table.uniques) == len(live_table.uniques)
        for column in dump_table.columns:
            live_column = live_table.column(column.name)
            assert column.nullable == live_column.nullable
            assert column.data_kind == live_column.data_kind
            assert column.enum_values == live_column.enum_values
        assert {trigger.function for trigger in dump_table.triggers} == {
            trigger.function for trigger in live_table.triggers
        }


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_dump_only_generation_passes() -> None:
    assert DSN
    subprocess.run(
        [
            "psql",
            DSN,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            str(ROOT / "examples/saas-billing/schema.sql"),
        ],
        check=True,
        capture_output=True,
    )
    catalog = load_dump(ROOT / "examples/saas-billing/schema.sql")

    connection = psycopg.connect(DSN)
    writer = PostgresRowWriter(connection)
    try:
        generated = GenerateDataset(catalog, writer, rows=25, seed=11).execute()
    finally:
        writer.close()

    connection = psycopg.connect(DSN)
    try:
        verify = VerifyDataset(
            catalog, PostgresQueryRunner(connection), YamlSpecLoader(), PostgresDialect(), seed=11
        )
        report = verify.execute(
            VerifyRequest(
                spec_path=str(ROOT / "examples/saas-billing/plan.yml"),
                dataset_hash=generated.dataset_hash,
                plan=generated.plan,
            )
        )
    finally:
        connection.close()

    assert report.verdict == "PASS"


def test_trigger_rules_are_segmentation_only() -> None:
    dump = """
    CREATE TABLE accounts (
        id integer PRIMARY KEY,
        amount numeric,
        subtotal numeric,
        tax numeric,
        total numeric,
        status text,
        updated_at timestamptz
    );
    CREATE FUNCTION enforce() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.amount < 0 THEN
            RAISE EXCEPTION 'amount must be non-negative';
        END IF;
        IF NEW.status NOT IN ('open', 'closed') THEN
            RAISE EXCEPTION 'bad status';
        END IF;
        NEW.total := NEW.subtotal + NEW.tax;
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER accounts_enforce BEFORE INSERT OR UPDATE ON accounts
        FOR EACH ROW EXECUTE FUNCTION enforce();
    """
    catalog = DumpLoader().load(dump)
    rules = {(rule.kind, rule.expression): rule for rule in catalog.rules}

    guard = rules[("guard", "amount >= 0")]
    assert guard.applied is False
    assert guard.category == "segmentation"
    assert guard.table == "public.accounts"
    assert guard.columns == ["amount"]
    assert guard.provenance is not None
    assert guard.provenance.kind == "trigger"
    assert guard.confidence >= 0.9

    status = rules[("guard", "status IN ('open', 'closed')")]
    assert status.applied is False

    derived = rules[("derived", "total = subtotal + tax")]
    assert derived.applied is False
    assert derived.columns == ["subtotal", "tax", "total"]

    noisy = rules[("derived", "updated_at = now()")]
    assert noisy.applied is False
    assert noisy.confidence < 0.9
    assert noisy.columns == ["updated_at"]

    assert catalog.table("public.accounts").checks == []


def test_function_rules_are_candidates_not_applied() -> None:
    dump = """
    CREATE TABLE t (id integer PRIMARY KEY, amount numeric);
    CREATE FUNCTION validate_amount(value numeric) RETURNS void LANGUAGE plpgsql AS $$
    BEGIN
        IF value < 0 THEN
            RAISE EXCEPTION 'negative';
        END IF;
    END;
    $$;
    """
    catalog = DumpLoader().load(dump)
    rule = catalog.rules[0]

    assert rule.kind == "guard"
    assert rule.category == "segmentation"
    assert rule.table == ""
    assert rule.applied is False
    assert rule.provenance is not None
    assert rule.provenance.kind == "function"
    assert catalog.table("public.t").checks == []


def test_compound_condition_is_a_low_confidence_candidate() -> None:
    dump = """
    CREATE TABLE t (id integer PRIMARY KEY, a integer, b integer);
    CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.a > 0 AND NEW.b > 0 THEN
            RAISE EXCEPTION 'both';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER t_f BEFORE INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();
    """
    catalog = DumpLoader().load(dump)
    rule = catalog.rules[0]

    assert rule.expression == "NOT (a > 0 AND b > 0)"
    assert rule.applied is False
    assert rule.confidence < 0.9
    assert catalog.table("public.t").checks == []


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason="PGPROOF_TEST_DSN is not set")
def test_trigger_rules_do_not_constrain_generation() -> None:
    assert DSN
    schema = "pgproof_rules_test"
    dump = f"""
    DROP SCHEMA IF EXISTS {schema} CASCADE;
    CREATE SCHEMA {schema};
    SET search_path TO {schema};
    CREATE TABLE lines (
        id integer PRIMARY KEY,
        quantity integer NOT NULL,
        unit_price numeric NOT NULL,
        amount numeric NOT NULL
    );
    CREATE FUNCTION enforce() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.quantity <= 0 THEN
            RAISE EXCEPTION 'quantity must be positive';
        END IF;
        NEW.amount := NEW.quantity * NEW.unit_price;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER lines_enforce BEFORE INSERT ON lines
        FOR EACH ROW EXECUTE FUNCTION enforce();
    """
    subprocess.run(
        ["psql", DSN, "-q", "-v", "ON_ERROR_STOP=1", "-c", dump],
        check=True,
        capture_output=True,
    )
    catalog = DumpLoader().load(dump)
    table = catalog.table(f"{schema}.lines")

    assert table.checks == []
    assert all(rule.applied is False for rule in catalog.rules)
    assert all(rule.category == "segmentation" for rule in catalog.rules)

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
    assert all(result.kind != "check" for result in report.results)


def test_nested_and_else_rules_are_not_overgeneralized() -> None:
    dump = """
    CREATE TABLE t (id integer PRIMARY KEY, a integer, b integer, c integer);
    CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.a > 0 THEN
            IF NEW.b > 0 THEN
                RAISE EXCEPTION 'nested';
            END IF;
        END IF;
        IF NEW.c > 0 THEN
            NULL;
        ELSE
            RAISE EXCEPTION 'else branch';
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER t_f BEFORE INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();
    """
    catalog = DumpLoader().load(dump)

    assert catalog.rules == []
    assert catalog.table("public.t").checks == []


def test_exclusion_constraint_is_a_declared_constraint() -> None:
    dump = """
    CREATE TABLE booking (
        id integer PRIMARY KEY,
        room integer NOT NULL,
        during tsrange NOT NULL,
        active boolean NOT NULL DEFAULT true,
        CONSTRAINT no_overlap EXCLUDE USING gist (room WITH =, during WITH &&) WHERE (active)
    );
    """
    catalog = DumpLoader().load(dump)
    table = catalog.table("public.booking")
    exclusion = table.exclusions[0]

    assert exclusion.name == "no_overlap"
    assert exclusion.method == "gist"
    assert [(element.column, element.operator) for element in exclusion.elements] == [
        ("room", "="),
        ("during", "&&"),
    ]
    assert exclusion.predicate == "(active)"
    assert exclusion.provenance is not None

    invariant = next(
        item
        for item in invariants_from_catalog(catalog, PostgresDialect())
        if item.kind == InvariantKind.EXCLUSION
    )
    assert 'a."room" = b."room"' in invariant.violation_sql
    assert 'a."during" && b."during"' in invariant.violation_sql
    assert '(a."active")' in invariant.violation_sql
    assert invariant.provenance == "CREATE TABLE booking ("


def test_exclusion_constraint_from_alter_table() -> None:
    dump = """
    CREATE TABLE booking (id integer PRIMARY KEY, room integer, during tsrange);
    ALTER TABLE booking
        ADD CONSTRAINT no_overlap EXCLUDE USING gist (room WITH =, during WITH &&);
    """
    catalog = DumpLoader().load(dump)
    exclusion = catalog.table("public.booking").exclusions[0]

    assert [element.operator for element in exclusion.elements] == ["=", "&&"]
    assert exclusion.predicate is None


def test_view_filter_is_an_aggregate_candidate() -> None:
    dump = """
    CREATE TABLE orders (id integer PRIMARY KEY, status text);
    CREATE VIEW paid_orders AS
        SELECT id FROM orders WHERE status = 'paid';
    """
    catalog = DumpLoader().load(dump)
    rule = next(item for item in catalog.rules if item.kind == "filter")

    assert rule.table == "public.orders"
    assert rule.expression == "status = 'paid'"
    assert rule.scope == "aggregate"
    assert rule.applied is False
    assert rule.provenance is not None
    assert rule.provenance.kind == "view"
    assert catalog.table("public.orders").checks == []


def test_partial_unique_index_keeps_predicate_and_warns_on_expression() -> None:
    dump = """
    CREATE TABLE public.users (
        id integer PRIMARY KEY,
        email text NOT NULL,
        seat integer NOT NULL,
        deleted_at timestamp NULL
    );
    CREATE UNIQUE INDEX uq_users_seat ON public.users (seat)
        WHERE deleted_at IS NULL;
    CREATE UNIQUE INDEX uq_users_lower_email ON public.users (lower(email))
        WHERE deleted_at IS NULL;
    """
    catalog = DumpLoader().load(dump)
    users = catalog.table("public.users")

    predicate_index = next(
        item for item in users.uniques if item.name == "uq_users_seat"
    )
    assert predicate_index.columns == ["seat"]
    assert predicate_index.predicate is not None
    assert "deleted_at" in predicate_index.predicate
    assert any("expression" in warning for warning in catalog.warnings)
