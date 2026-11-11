"""Deterministic data generation.

The generator is pure Python: it never connects to a database. It receives a
catalog and a plan, and yields rows. Determinism is a product promise, so every
random source is seeded from (plan.seed, table, column).
"""

from __future__ import annotations

import math
import random
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from .distribution import build_distribution, coerce_value, numeric_scale
from .exclusions import (
    ExclusionSpec,
    build_exclusion_specs,
    first_conflict,
    next_range,
    next_value,
    predicate_columns,
    predicate_matches,
    range_subtype,
)
from .invariants import LocalRules, infer_semantic, parse_local_rules
from .ir import Catalog, Column, DataKind, Table, all_partition_columns
from .planner import Plan
from .profile import Profile


class GenerationError(RuntimeError):
    pass


_BASE_DATE = date(2025, 1, 1)
_BASE_DATETIME = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

_FIRST_NAMES = [
    "Ana", "Luis", "María", "Carlos", "Sofía", "Jorge", "Elena", "Miguel",
    "Lucía", "Diego", "Valeria", "Andrés", "Camila", "Ricardo", "Paula",
]
_LAST_NAMES = [
    "García", "Hernández", "López", "Martínez", "Rodríguez", "Pérez",
    "Sánchez", "Ramírez", "Torres", "Flores", "Rivera", "Gómez",
]
_CITIES = [
    "Ciudad de México", "Guadalajara", "Monterrey", "Puebla", "Querétaro",
    "Mérida", "Tijuana", "León", "Toluca", "Cancún",
]
_REGIONS = [
    "CDMX", "Jalisco", "Nuevo León", "Puebla", "Querétaro", "Yucatán",
    "Baja California", "Guanajuato", "Estado de México", "Quintana Roo",
]
_COUNTRIES = ["México", "México", "México", "Colombia", "Chile", "España"]
_COMPANIES = [
    "Aurora Labs", "Norte Digital", "Cobalto", "Mercado Sur", "Punto Base",
    "Delta Software", "Rumbo", "Claro Studio", "Fábrica de Datos", "Zenit",
]
_STREETS = ["Av. Reforma", "Calle Hidalgo", "Av. Juárez", "Calle Allende", "Av. Insurgentes"]
_DOMAINS = ["example.com", "example.org", "example.mx"]
_LOREM = [
    "Registro generado para pruebas de integración.",
    "Documento de ejemplo sin datos personales reales.",
    "Fila sintética reproducible a partir de una semilla.",
    "Caso borde incluido a propósito para ejercitar el esquema.",
]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _freeze(value: Any) -> Any:
    if isinstance(value, (list, dict, set)):
        return repr(value)
    return value


def _leaf_paths(nodes: list[Any], path: list[Any] | None = None) -> Iterator[list[Any]]:
    current = list(path or [])
    for node in nodes:
        branch = current + [node]
        if node.children:
            yield from _leaf_paths(node.children, branch)
        else:
            yield branch


def _tuple_in_range(values: list[Any], lower: list[Any], upper: list[Any]) -> bool:
    def lower_key(value: Any) -> tuple:
        return (0,) if value is None else (1, value)

    def upper_key(value: Any) -> tuple:
        return (2,) if value is None else (1, value)

    padded_lower = list(lower) + [None] * (len(values) - len(lower))
    padded_upper = list(upper) + [None] * (len(values) - len(upper))
    try:
        return tuple(lower_key(value) for value in values) >= tuple(
            lower_key(value) for value in padded_lower
        ) and tuple(upper_key(value) for value in values) < tuple(
            upper_key(value) for value in padded_upper
        )
    except TypeError:
        return True


def _satisfies(path: list[Any], values: dict[str, Any]) -> bool:
    for node in path:
        if node.strategy == "range":
            current = [values.get(name) for name in node.columns]
            if any(value is None for value in current):
                return False
            if not _tuple_in_range(current, node.lower or [], node.upper or []):
                return False
        elif node.strategy == "list":
            current = tuple(values.get(name) for name in node.columns)
            if not any(tuple(row) == current for row in node.values):
                return False
    return True


@dataclass
class GeneratedTable:
    key: str
    columns: list[str]
    rows: list[tuple[Any, ...]]


class Generator:
    def __init__(self, catalog: Catalog, plan: Plan, profile: Profile | None = None) -> None:
        self.catalog = catalog
        self.plan = plan
        self.profile = profile
        self.rules: dict[str, LocalRules] = {
            table.key: parse_local_rules(table) for table in catalog.tables
        }
        self.distributions: dict[tuple[str, str], Any] = {}
        if profile is not None:
            for table in catalog.tables:
                stats = profile.tables.get(table.key)
                if not stats:
                    continue
                for column in table.columns:
                    column_stats = stats.columns.get(column.name)
                    if not column_stats:
                        continue
                    distribution = build_distribution(column_stats, column)
                    if distribution is not None:
                        self.distributions[(table.key, column.name)] = distribution
        self.pools: dict[tuple[str, tuple[str, ...]], list[tuple[Any, ...]]] = {}
        self._rngs: dict[str, random.Random] = {}
        self._partition_counters: dict[str, int] = {}
        self._unique_columns: dict[str, set[str]] = {}
        self._unique_keys: dict[str, list[tuple[tuple[str, ...], str | None]]] = {}
        self._seen_keys: dict[tuple[str, tuple[str, ...]], set[tuple]] = {}
        for table in catalog.tables:
            self._unique_columns[table.key] = {
                unique.columns[0]
                for unique in table.uniques
                if len(unique.columns) == 1 and unique.predicate is None
            }
            self._unique_keys[table.key] = [
                (tuple(unique.columns), unique.predicate)
                for unique in table.uniques
            ]
        self.scoped_fks: dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]] = {}
        self.scope_specs: dict[str, list[tuple[tuple[str, ...], tuple[str, ...]]]] = {}
        self.scope_index: dict[
            tuple[str, tuple[str, ...], tuple[str, ...]], list[tuple[tuple, tuple]]
        ] = {}
        for table in catalog.tables:
            child_names = {column.name for column in table.columns}
            child_fk_columns = {
                column for fk in table.foreign_keys for column in fk.child_columns
            }
            for fk in table.foreign_keys:
                parent = catalog.table(fk.parent_key)
                parent_names = {column.name for column in parent.columns}
                parent_fk_columns = {
                    column for parent_fk in parent.foreign_keys for column in parent_fk.child_columns
                }
                # A scope column is shared by both tables and is itself a
                # foreign key in both (e.g. tenant_id). This deliberately
                # ignores common columns such as id or name, which are not
                # scopes and must never be overwritten.
                shared = tuple(
                    sorted(
                        (child_names & parent_names & child_fk_columns & parent_fk_columns)
                        - set(fk.child_columns)
                    )
                )
                if not shared:
                    continue
                parent_columns = tuple(fk.parent_columns)
                self.scoped_fks[(table.key, fk.name)] = (shared, parent_columns)
                specs = self.scope_specs.setdefault(fk.parent_key, [])
                spec = (parent_columns, shared)
                if spec not in specs:
                    specs.append(spec)
        self.exclusion_specs: dict[str, list[ExclusionSpec]] = {}
        self._exclusion_columns: dict[str, list[str]] = {}
        self._exclusion_history: dict[str, list[dict[str, Any]]] = {}
        for table in catalog.tables:
            specs = build_exclusion_specs(table)
            if not specs:
                continue
            self.exclusion_specs[table.key] = specs
            columns = {
                element.column
                for spec in specs
                for element in spec.elements
            }
            for spec in specs:
                columns |= predicate_columns(spec.predicate)
            self._exclusion_columns[table.key] = sorted(columns)
        self._build_pools()

    # ------------------------------------------------------------------ rng
    def rng_for(self, key: str) -> random.Random:
        if key not in self._rngs:
            self._rngs[key] = random.Random(f"{self.plan.seed}:{key}")
        return self._rngs[key]

    # ---------------------------------------------------------------- pools
    def _build_pools(self) -> None:
        for table in self.catalog.tables:
            rows = self.plan.tables[table.key].rows
            key_sets: list[tuple[str, ...]] = []
            if table.primary_key:
                key_sets.append(tuple(table.primary_key))
            for unique in table.uniques:
                key_sets.append(tuple(unique.columns))
            for columns in key_sets:
                rng = self.rng_for(f"{table.key}:pool")
                values = [
                    tuple(self._key_value(table.column(name), table, index, rng) for name in columns)
                    for index in range(rows)
                ]
                self.pools[(table.key, columns)] = values

        # Foreign keys may list parent columns in a different order than the
        # primary key, and may point at a unique key. Add reordered aliases.
        for table in self.catalog.tables:
            for fk in table.foreign_keys:
                target = (fk.parent_key, tuple(fk.parent_columns))
                if target in self.pools:
                    continue
                for (parent_key, columns), values in list(self.pools.items()):
                    if parent_key != fk.parent_key or set(columns) != set(fk.parent_columns):
                        continue
                    positions = [columns.index(name) for name in fk.parent_columns]
                    self.pools[target] = [
                        tuple(value[position] for position in positions) for value in values
                    ]
                    break

    def _key_value(self, column: Column, table: Table, index: int, rng: random.Random) -> Any:
        kind = column.data_kind
        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            return index + 1
        if kind == DataKind.UUID:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.plan.seed}:{table.key}:{index}"))
        if kind == DataKind.TEXT:
            return f"{_slug(table.name)}_{index + 1:06d}"
        if kind in (DataKind.DATE, DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
            return _BASE_DATE + timedelta(days=index)
        return index + 1

    # ------------------------------------------------------------ generation
    def generate_table(self, table: Table) -> Iterator[tuple[Any, ...]]:
        columns = table.writable_columns
        rows = self.plan.tables[table.key].rows
        for index in range(rows):
            yield self._make_row(table, columns, index)

    def _make_row(self, table: Table, columns: list[Column], index: int) -> tuple[Any, ...]:
        values: dict[str, Any] = {}
        rng = self.rng_for(f"{table.key}:rows")

        partition_values = self._partition_values(table, index, rng)
        for name, value in partition_values.items():
            values[name] = value

        primary_key = tuple(table.primary_key)
        pk_pool = self.pools.get((table.key, primary_key)) if primary_key else None
        if pk_pool is not None and index < len(pk_pool):
            for name, value in zip(primary_key, pk_pool[index]):
                if name in partition_values:
                    continue
                values[name] = value

        nullable = {column.name: column.nullable for column in table.columns}
        ordered_fks = sorted(
            table.foreign_keys,
            key=lambda fk: (table.key, fk.name) in self.scoped_fks,
        )
        for fk in ordered_fks:
            if all(name in values for name in fk.child_columns):
                continue
            scope = self.scoped_fks.get((table.key, fk.name))
            if scope and self._assign_scoped_fk(fk, scope, values, nullable, rng):
                continue
            pool = self.pools.get((fk.parent_key, tuple(fk.parent_columns)))
            if not pool:
                if all(nullable.get(name, True) for name in fk.child_columns):
                    for name in fk.child_columns:
                        values[name] = None
                    continue
                raise GenerationError(
                    f"{table.key}: cannot populate foreign key {fk.name} because "
                    f"{fk.parent_key}{tuple(fk.parent_columns)} has no primary or unique key"
                )
            if all(nullable.get(name, True) for name in fk.child_columns) and rng.random() < 0.04:
                for name in fk.child_columns:
                    values[name] = None
                continue
            parent_values = pool[rng.randrange(len(pool))]
            for name, value in zip(fk.child_columns, parent_values):
                values[name] = value

        for column in columns:
            if column.name in values:
                continue
            values[column.name] = self._value(column, table, index, rng)

        self._apply_derived(table, values)
        self._fix_orderings(table, values)
        return tuple(values.get(column.name) for column in columns)

    def _apply_derived(self, table: Table, values: dict[str, Any]) -> None:
        for target, left, operator, right in self.rules[table.key].derived:
            a = values.get(left)
            if operator == "copy":
                if a is not None:
                    values[target] = a
                continue
            b = values.get(right)
            if a is None or b is None:
                continue
            try:
                if operator == "*":
                    values[target] = a * b
                elif operator == "+":
                    values[target] = a + b
                elif operator == "-":
                    values[target] = a - b
                elif operator == "/" and b != 0:
                    values[target] = a / b
            except (TypeError, ArithmeticError):
                continue

    def _assign_scoped_fk(
        self,
        fk,
        scope: tuple[tuple[str, ...], tuple[str, ...]],
        values: dict[str, Any],
        nullable: dict[str, bool],
        rng: random.Random,
    ) -> bool:
        """Keep a child's scope columns consistent with the row it references.

        A multi-tenant child (``tenant_id``) that points at a parent which also
        has ``tenant_id`` must reference a parent from the same tenant. When the
        parent has no row for that tenant we adopt the parent's scope instead,
        which keeps the foreign key valid.
        """
        scope_columns, parent_columns = scope
        entries = self.scope_index.get((fk.parent_key, parent_columns, scope_columns))
        if not entries:
            return False

        if all(nullable.get(name, True) for name in fk.child_columns) and rng.random() < 0.04:
            for name in fk.child_columns:
                values[name] = None
            return True

        current = tuple(values.get(name) for name in scope_columns)
        matching = (
            [entry for entry in entries if entry[0] == current]
            if all(value is not None for value in current)
            else []
        )
        candidates = matching or entries
        chosen = candidates[rng.randrange(len(candidates))]
        for name, value in zip(scope_columns, chosen[0]):
            values[name] = value
        for name, value in zip(fk.child_columns, chosen[1]):
            values[name] = value
        return True

    def _index_scope(self, table: Table, row: tuple[Any, ...]) -> None:
        specs = self.scope_specs.get(table.key)
        if not specs:
            return
        names = [column.name for column in table.writable_columns]
        values = dict(zip(names, row))
        for key_columns, scope_columns in specs:
            entry = (
                tuple(values.get(name) for name in scope_columns),
                tuple(values.get(name) for name in key_columns),
            )
            index_key = (table.key, key_columns, scope_columns)
            self.scope_index.setdefault(index_key, []).append(entry)

    def _partition_values(
        self, table: Table, index: int, rng: random.Random
    ) -> dict[str, Any]:
        if not table.is_partitioned or not table.partitions:
            return {}

        paths = [
            path
            for path in _leaf_paths(table.partitions)
            if any(node.strategy in {"range", "list"} for node in path)
        ]
        if not paths:
            return {}

        all_columns = all_partition_columns(table)
        key_only = set(table.primary_key) == set(all_columns)

        if key_only and len(all_columns) == 1:
            return self._sequence_partition_values(table, paths, all_columns[0])

        chosen = rng.choices(
            paths,
            weights=[max(path[-1].estimated_rows, 1) for path in paths],
            k=1,
        )[0]

        for _ in range(30):
            values: dict[str, Any] = {}
            for node in chosen:
                if node.strategy == "range" and node.columns:
                    sampled = self._sample_range_tuple(table, node, rng)
                elif node.strategy == "list" and node.values:
                    sampled = list(rng.choice(node.values))
                else:
                    continue
                for name, value in zip(node.columns, sampled):
                    values[name] = value
            if _satisfies(chosen, values):
                return values

        fallback: dict[str, Any] = {}
        for node in chosen:
            if node.strategy == "range" and node.lower:
                for name, value in zip(node.columns, node.lower):
                    if value is not None:
                        fallback[name] = value
            elif node.strategy == "list" and node.values:
                for name, value in zip(node.columns, node.values[0]):
                    fallback[name] = value
        return fallback

    def _sequence_partition_values(
        self, table: Table, paths: list[list[Any]], column_name: str
    ) -> dict[str, Any]:
        counter = self._partition_counters.get(table.key, 0)
        self._partition_counters[table.key] = counter + 1
        remaining = counter
        column = table.column(column_name)
        for path in paths:
            node = next(
                (item for item in path if item.strategy in {"range", "list"} and item.columns),
                None,
            )
            if node is None:
                continue
            capacity = self._capacity(column, node)
            if remaining < capacity:
                if node.strategy == "list":
                    return {column_name: node.values[remaining][0]}
                lower = node.lower[0] if node.lower else None
                upper = node.upper[0] if node.upper else None
                return {
                    column_name: self._nth_range_value(column, lower, upper, remaining)
                }
            remaining -= capacity
        raise GenerationError(
            f"{table.key}: not enough distinct partition-key values for "
            "a partition-key-only primary key"
        )

    def _capacity(self, column: Column, node: Any) -> int:
        if node.strategy == "list":
            return len(node.values)
        lower = node.lower[0] if node.lower else None
        upper = node.upper[0] if node.upper else None
        return self._range_capacity(column, lower, upper)

    def _sample_range_tuple(
        self, table: Table, node: Any, rng: random.Random
    ) -> list[Any]:
        lower = list(node.lower or [None] * len(node.columns))
        upper = list(node.upper or [None] * len(node.columns))
        for _ in range(20):
            values = [
                self._sample_range(table.column(name), low, high, rng)
                for name, low, high in zip(node.columns, lower, upper)
            ]
            if _tuple_in_range(values, lower, upper):
                return values
        return lower

    def _sample_range(
        self,
        column: Column,
        lower: Any,
        upper: Any,
        rng: random.Random,
    ) -> Any:
        kind = column.data_kind
        if kind == DataKind.DATE:
            low = (
                lower
                if isinstance(lower, date) and not isinstance(lower, datetime)
                else date(2020, 1, 1)
            )
            high = (
                upper
                if isinstance(upper, date) and not isinstance(upper, datetime)
                else date(2030, 1, 1)
            )
            days = (high - low).days
            if days <= 0:
                return low
            return low + timedelta(days=rng.randrange(days))

        if kind in (DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
            zone = UTC if kind == DataKind.TIMESTAMPTZ else None
            low = lower if isinstance(lower, datetime) else datetime(2020, 1, 1, tzinfo=zone)
            high = (
                upper if isinstance(upper, datetime) else datetime(2030, 1, 1, tzinfo=zone)
            )
            seconds = int((high - low).total_seconds())
            if seconds <= 0:
                return low
            return low + timedelta(seconds=rng.randrange(seconds))

        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            low = int(lower) if lower is not None else 0
            high = int(upper) if upper is not None else low + 1_000_000
            if high <= low:
                return low
            return rng.randrange(low, high)

        if kind in (DataKind.NUMERIC, DataKind.FLOAT):
            low = float(lower) if lower is not None else 0.0
            high = float(upper) if upper is not None else low + 1_000_000.0
            if high <= low:
                return low
            value = rng.uniform(low, high)
            if kind == DataKind.NUMERIC:
                return Decimal(str(round(value, numeric_scale(column.sql_type))))
            return value

        return None

    def _range_capacity(self, column: Column, lower: Any, upper: Any) -> int:
        kind = column.data_kind
        if kind == DataKind.DATE:
            low = lower if isinstance(lower, date) else date(2020, 1, 1)
            high = upper if isinstance(upper, date) else date(2030, 1, 1)
            return max(0, (high - low).days)
        if kind in (DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
            low = lower if isinstance(lower, datetime) else datetime(2020, 1, 1)
            high = upper if isinstance(upper, datetime) else datetime(2030, 1, 1)
            return max(0, int((high - low).total_seconds()))
        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            low = int(lower) if lower is not None else 0
            high = int(upper) if upper is not None else low + 1_000_000
            return max(0, high - low)
        if kind in (DataKind.NUMERIC, DataKind.FLOAT):
            low = float(lower) if lower is not None else 0.0
            high = float(upper) if upper is not None else low + 1_000_000.0
            step = 10 ** -numeric_scale(column.sql_type) if kind == DataKind.NUMERIC else 1.0
            return max(0, int((high - low) / step))
        return 0

    def _nth_range_value(
        self, column: Column, lower: Any, upper: Any, offset: int
    ) -> Any:
        kind = column.data_kind
        if kind == DataKind.DATE:
            low = lower if isinstance(lower, date) else date(2020, 1, 1)
            return low + timedelta(days=offset)
        if kind in (DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
            low = lower if isinstance(lower, datetime) else datetime(2020, 1, 1)
            return low + timedelta(seconds=offset)
        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            low = int(lower) if lower is not None else 0
            return low + offset
        if kind == DataKind.NUMERIC:
            low = Decimal(str(lower)) if lower is not None else Decimal(0)
            step = Decimal(1).scaleb(-numeric_scale(column.sql_type))
            return low + step * offset
        if kind == DataKind.FLOAT:
            low = float(lower) if lower is not None else 0.0
            return low + offset
        return lower

    def _fix_orderings(self, table: Table, values: dict[str, Any]) -> None:
        for left, right, strict in self.rules[table.key].orderings:
            first = values.get(left)
            second = values.get(right)
            if first is None or second is None:
                continue
            try:
                satisfied = first > second or (not strict and first == second)
            except TypeError:
                continue
            if satisfied:
                continue
            try:
                if isinstance(second, datetime):
                    values[left] = second + timedelta(seconds=1 if strict else 0)
                elif isinstance(second, date):
                    values[left] = second + timedelta(days=1 if strict else 0)
                elif isinstance(second, Decimal):
                    values[left] = second + Decimal(1 if strict else 0)
                elif isinstance(second, int):
                    values[left] = second + (1 if strict else 0)
                elif isinstance(second, float):
                    values[left] = second + (1.0 if strict else 0.0)
            except TypeError:
                continue

    # ------------------------------------------------------------- values
    def _value(self, column: Column, table: Table, index: int, rng: random.Random) -> Any:
        distribution = self.distributions.get((table.key, column.name))
        if distribution is not None:
            sampled = distribution.sample(rng)
            if sampled is None:
                return None
            sampled = coerce_value(column, sampled)
            return self._unique_value(
                table,
                column,
                index,
                self._apply_rules(column, sampled, self.rules[table.key], rng),
            )

        if column.nullable and rng.random() < 0.02:
            return None

        if column.enum_values:
            return rng.choice(column.enum_values)

        semantic = column.semantic or infer_semantic(column)
        kind = column.data_kind

        if kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT):
            value = self._int_value(semantic, index, rng)
        elif kind == DataKind.NUMERIC:
            value = self._numeric_value(semantic, rng)
        elif kind == DataKind.FLOAT:
            value = float(self._numeric_value(semantic, rng))
        elif kind == DataKind.BOOLEAN:
            value = rng.random() < 0.5
        elif kind == DataKind.UUID:
            value = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.plan.seed}:{table.key}:{column.name}:{index}"))
        elif kind == DataKind.DATE:
            value = self._date_value(semantic, rng)
        elif kind in (DataKind.TIMESTAMP, DataKind.TIMESTAMPTZ):
            value = self._timestamp_value(semantic, rng)
        elif kind == DataKind.TIME:
            value = time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59))
        elif kind == DataKind.JSON:
            value = '{"source": "smoker", "synthetic": true}'
        elif kind == DataKind.RANGE:
            value = self._range_literal(column, index)
        elif kind == DataKind.BYTEA:
            value = b"smoker"
        elif kind == DataKind.INET:
            value = f"192.168.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
        elif kind == DataKind.ARRAY:
            value = "{}"
        elif kind == DataKind.TEXT:
            value = self._text_value(column, table, index, semantic, rng)
        else:
            value = f"{column.name}_{index}"

        return self._unique_value(
            table, column, index, self._apply_rules(column, value, self.rules[table.key], rng)
        )

    def _unique_value(self, table: Table, column: Column, index: int, value: Any) -> Any:
        """Make single-column unique values unique by construction.

        Truncation to a varchar length is the classic way to turn unique values
        into duplicates, so the discriminator is appended after truncation.
        """
        if value is None or isinstance(value, bool):
            return value
        if column.name not in self._unique_columns.get(table.key, set()):
            return value
        if isinstance(value, (int, float, Decimal)):
            return value
        if isinstance(value, str):
            suffix = f"-{index + 1}"
            limit = column.max_length or 64
            if len(value) + len(suffix) > limit:
                value = value[: max(0, limit - len(suffix))]
            return (value + suffix)[:limit]
        return value

    def _int_value(self, semantic: str | None, index: int, rng: random.Random) -> int:
        if semantic == "quantity":
            return rng.randint(1, 100)
        if semantic == "percentage":
            return rng.randint(0, 100)
        if semantic == "amount":
            return rng.randint(1, 100_000)
        return rng.randint(1, 100_000)

    def _numeric_value(self, semantic: str | None, rng: random.Random) -> Decimal:
        if semantic == "percentage":
            return Decimal(str(round(rng.uniform(0, 100), 2)))
        if semantic == "quantity":
            return Decimal(rng.randint(1, 100))
        return Decimal(str(round(rng.uniform(1, 100_000), 2)))

    def _date_value(self, semantic: str | None, rng: random.Random) -> date:
        return _BASE_DATE - timedelta(days=rng.randint(0, 730))

    def _timestamp_value(self, semantic: str | None, rng: random.Random) -> datetime:
        return _BASE_DATETIME - timedelta(seconds=rng.randint(0, 63_072_000))

    def _range_literal(self, column: Column, index: int) -> str:
        subtype = range_subtype(column.sql_type)
        if subtype == "int":
            lower = index * 10 + 1
            return f"[{lower},{lower + 10})"
        if subtype == "numeric":
            lower = Decimal(index * 10)
            return f"[{format(lower, 'f')},{format(lower + 10, 'f')})"
        if subtype == "date":
            lower = _BASE_DATE + timedelta(days=index)
            upper = lower + timedelta(days=1)
            return f"[{lower.isoformat()},{upper.isoformat()})"
        if subtype == "timestamp":
            lower = _BASE_DATETIME + timedelta(hours=index)
            upper = lower + timedelta(hours=1)
            return f"[{lower.isoformat(sep=' ')},{upper.isoformat(sep=' ')})"
        return f"[{index},{index + 1})"

    def _text_value(
        self,
        column: Column,
        table: Table,
        index: int,
        semantic: str | None,
        rng: random.Random,
    ) -> str:
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)

        if semantic == "email":
            value = f"{_slug(first)}.{_slug(last)}{index}@{rng.choice(_DOMAINS)}"
        elif semantic == "first_name":
            value = first
        elif semantic == "last_name":
            value = last
        elif semantic == "full_name":
            value = f"{first} {last}"
        elif semantic == "phone":
            value = f"+52 55 {rng.randint(1000, 9999)} {rng.randint(1000, 9999)}"
        elif semantic == "rfc":
            value = (
                f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
                f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')}"
                f"{rng.randint(0, 999999):06d}"
                f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')}"
                f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')}"
                f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')}"
            )
        elif semantic == "curp":
            value = (
                f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ') * 4}"
                f"{rng.randint(0, 991231):06d}"
                f"{rng.choice('HM')}"
                f"{rng.choice(['DF', 'MC', 'NL', 'JC', 'PL'])}"
                f"{rng.choice('BCDFGHJKLMNÑPQRSTVWXYZ') * 3}"
                f"{rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')}"
                f"{rng.randint(0, 9)}"
            )
        elif semantic == "clabe":
            value = f"{rng.randint(0, 999):03d}{rng.randint(0, 10**15 - 1):015d}"
        elif semantic == "address":
            value = f"{rng.choice(_STREETS)} {rng.randint(1, 2000)}, Col. Centro"
        elif semantic == "city":
            value = rng.choice(_CITIES)
        elif semantic == "region":
            value = rng.choice(_REGIONS)
        elif semantic == "country":
            value = rng.choice(_COUNTRIES)
        elif semantic == "postal_code":
            value = f"{rng.randint(0, 99999):05d}"
        elif semantic == "company":
            value = rng.choice(_COMPANIES)
        elif semantic == "url":
            value = f"https://{_slug(rng.choice(_COMPANIES))}.example.com"
        elif semantic == "ip":
            value = f"192.168.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
        elif semantic == "username":
            value = f"{_slug(first)}.{_slug(last)}{index}"
        elif semantic == "slug":
            value = f"{_slug(first)}-{_slug(last)}-{index}"
        elif semantic == "code":
            value = f"{column.name[:3].upper()}-{index + 1:06d}"
        elif semantic == "status":
            value = rng.choice(["active", "inactive", "pending"])
        elif semantic == "description":
            value = rng.choice(_LOREM)
        elif semantic == "title":
            value = f"{rng.choice(_LOREM).split('.')[0]} {index + 1}"
        else:
            value = f"{table.name}_{column.name}_{index + 1}"

        limit = column.max_length
        if limit and len(value) > limit:
            value = value[:limit]
        return value

    def _apply_rules(
        self, column: Column, value: Any, rules: LocalRules, rng: random.Random
    ) -> Any:
        if value is None:
            return None

        low, high, low_strict, high_strict = rules.ranges.get(
            column.name, (None, None, False, False)
        )
        if (low is not None or high is not None) and isinstance(value, (int, float, Decimal)):
            numeric = Decimal(str(value))
            low_decimal = Decimal(str(low)) if low is not None else None
            high_decimal = Decimal(str(high)) if high is not None else None
            below = low_decimal is not None and (
                numeric < low_decimal or (low_strict and numeric == low_decimal)
            )
            above = high_decimal is not None and (
                numeric > high_decimal or (high_strict and numeric == high_decimal)
            )
            if below or above:
                value = self._sample_in_range(
                    column, low, high, low_strict, high_strict, rng
                )

        allowed = rules.allowed.get(column.name)
        if allowed and isinstance(value, str) and value not in allowed:
            value = sorted(allowed)[rng.randrange(len(allowed))]

        limit = rules.maxlen.get(column.name)
        if limit and isinstance(value, str) and len(value) > limit:
            value = value[:limit]

        return value

    def _sample_in_range(
        self,
        column: Column,
        low: float | None,
        high: float | None,
        low_strict: bool,
        high_strict: bool,
        rng: random.Random,
    ) -> Any:
        is_int = column.data_kind in (DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT)
        lo = float(low) if low is not None else 0.0
        hi = float(high) if high is not None else lo + 100_000.0
        if low_strict:
            lo += 1.0 if is_int else 0.01
        if high_strict:
            hi -= 1.0 if is_int else 0.01
        hi = max(hi, lo)
        if is_int:
            return rng.randint(math.ceil(lo), math.floor(hi))
        return Decimal(str(round(rng.uniform(lo, hi), 2)))

    # ---------------------------------------------------------- validation
    def validate_row(self, table: Table, row: tuple[Any, ...]) -> str | None:
        columns = table.writable_columns
        rules = self.rules[table.key]
        by_name = {column.name: value for column, value in zip(columns, row)}

        for column in columns:
            value = by_name[column.name]
            if value is None:
                if not column.nullable:
                    return f"{column.name} is NOT NULL"
                continue
            if column.enum_values and value not in column.enum_values:
                return f"{column.name}={value!r} is not a valid enum value"
            if isinstance(value, str):
                limit = column.max_length
                if limit and len(value) > limit:
                    return f"{column.name} exceeds {limit} characters"
                rule_limit = rules.maxlen.get(column.name)
                if rule_limit and len(value) > rule_limit:
                    return f"{column.name} exceeds check length {rule_limit}"
                allowed = rules.allowed.get(column.name)
                if allowed and value not in allowed:
                    return f"{column.name}={value!r} is not in {sorted(allowed)}"
            low, high, low_strict, high_strict = rules.ranges.get(
                column.name, (None, None, False, False)
            )
            if (low is not None or high is not None) and isinstance(
                value, (int, float, Decimal)
            ):
                numeric = Decimal(str(value))
                if low is not None and (
                    numeric < Decimal(str(low))
                    or (low_strict and numeric == Decimal(str(low)))
                ):
                    return f"{column.name}={value} violates > {low}"
                if high is not None and (
                    numeric > Decimal(str(high))
                    or (high_strict and numeric == Decimal(str(high)))
                ):
                    return f"{column.name}={value} violates < {high}"
        for left, right, strict in rules.orderings:
            first = by_name.get(left)
            second = by_name.get(right)
            if first is None or second is None:
                continue
            try:
                if strict and not first > second:
                    return f"{left} must be > {right}"
                if not strict and not first >= second:
                    return f"{left} must be >= {right}"
            except TypeError:
                return f"{left} and {right} are not comparable"
        return None

    def generate_validated(self, table: Table, attempts: int = 50) -> Iterator[tuple[Any, ...]]:
        columns = table.writable_columns
        rows = self.plan.tables[table.key].rows
        for index in range(rows):
            last_error = "unknown"
            for _ in range(attempts):
                row = self._make_row(table, columns, index)
                row = self._enforce_unique_keys(table, row, index)
                row = self._enforce_exclusions(table, row, index)
                error = self.validate_row(table, row)
                if error is None:
                    self._record_exclusions(table, row)
                    break
                last_error = error
            else:
                raise GenerationError(
                    f"{table.key} row {index + 1}: could not satisfy local rules "
                    f"after {attempts} attempts ({last_error})"
                )
            self._index_scope(table, row)
            yield row

    def _enforce_exclusions(
        self, table: Table, row: tuple[Any, ...], index: int
    ) -> tuple[Any, ...]:
        specs = self.exclusion_specs.get(table.key)
        if not specs:
            return row
        unsupported = [spec.name for spec in specs if not spec.supported]
        if unsupported:
            raise GenerationError(
                f"{table.key}: exclusion constraint(s) "
                f"{', '.join(unsupported)} use operators smoker cannot generate against"
            )
        columns = table.writable_columns
        names = [column.name for column in columns]
        values = dict(zip(names, row))
        history = self._exclusion_history.setdefault(table.key, [])
        for attempt in range(200):
            conflict = first_conflict(specs, values, history)
            if conflict is None:
                return tuple(values[name] for name in names)
            spec, previous = conflict
            target = self._exclusion_target(table, spec)
            if target is None:
                raise GenerationError(
                    f"{table.key} row {index + 1}: exclusion {spec.name} conflicts "
                    "and there is no non-key column to adjust"
                )
            column = table.column(target)
            if target in spec.range_columns:
                candidate = next_range(
                    column, values.get(target), previous.get(target)
                )
            else:
                candidate = next_value(
                    column, values.get(target), index, attempt, self.plan.seed
                )
            if candidate is None:
                raise GenerationError(
                    f"{table.key} row {index + 1}: cannot find a value for "
                    f"{target} that satisfies exclusion {spec.name}"
                )
            values[target] = candidate
        raise GenerationError(
            f"{table.key} row {index + 1}: could not satisfy exclusion constraints "
            "after 200 adjustments"
        )

    def _exclusion_target(self, table: Table, spec: ExclusionSpec) -> str | None:
        fk_columns = {
            column for fk in table.foreign_keys for column in fk.child_columns
        }
        unique_columns = {
            column for unique in table.uniques for column in unique.columns
        }
        rules = self.rules.get(table.key)
        rule_columns: set[str] = set()
        if rules is not None:
            for target, left, operator, right in rules.derived:
                rule_columns.update((target, left))
                if operator != "copy":
                    rule_columns.add(right)
            for left, right, _strict in rules.orderings:
                rule_columns.update((left, right))
            rule_columns.update(
                column
                for column, allowed in rules.allowed.items()
                if allowed
            )
        blocked = (
            fk_columns
            | set(table.primary_key)
            | unique_columns
            | set(all_partition_columns(table))
            | rule_columns
            | predicate_columns(spec.predicate)
        )
        for name in spec.equality_columns:
            column = table.column(name)
            if name in blocked:
                continue
            if column.generated or column.identity:
                continue
            if column.data_kind == DataKind.BOOLEAN:
                continue
            return name
        for name in spec.range_columns:
            column = table.column(name)
            if name in blocked or column.generated or column.identity:
                continue
            return name
        return None

    def _record_exclusions(self, table: Table, row: tuple[Any, ...]) -> None:
        columns = self._exclusion_columns.get(table.key)
        if not columns:
            return
        names = [column.name for column in table.writable_columns]
        values = dict(zip(names, row))
        self._exclusion_history.setdefault(table.key, []).append(
            {name: values.get(name) for name in columns}
        )

    def _enforce_unique_keys(
        self, table: Table, row: tuple[Any, ...], index: int
    ) -> tuple[Any, ...]:
        """Make composite unique keys unique by construction.

        Single-column uniques are handled at value time; composite keys are
        checked here because a collision only appears when the tuple is built.
        """
        key_columns_list = self._unique_keys.get(table.key, [])
        if not key_columns_list:
            return row
        columns = table.writable_columns
        names = [column.name for column in columns]
        values = dict(zip(names, row))

        for key_columns, predicate in key_columns_list:
            if not key_columns:
                continue
            if predicate and not predicate_matches(predicate, values):
                continue
            store_key = (table.key, key_columns, predicate)
            seen = self._seen_keys.setdefault(store_key, set())
            signature = tuple(_freeze(values.get(name)) for name in key_columns)
            if signature not in seen:
                seen.add(signature)
                continue

            partition_columns = set(all_partition_columns(table))
            blocked = set(partition_columns) | (
                set(predicate_columns(predicate)) - set(key_columns)
            )
            candidates = [
                name
                for name in key_columns
                if isinstance(values.get(name), str) and name not in blocked
            ]
            if not candidates:
                candidates = [
                    name
                    for name in key_columns
                    if name not in blocked
                    and not any(name in fk.child_columns for fk in table.foreign_keys)
                    and not table.column(name).generated
                ]
            if not candidates:
                continue

            target = candidates[0]
            column = table.column(target)
            counter = 0
            while True:
                if isinstance(values.get(target), str):
                    base = str(values.get(target) or "")
                    limit = column.max_length or 64
                    suffix = (
                        f"-{index + 1}" if counter == 0 else f"-{index + 1}-{counter}"
                    )
                    candidate = (
                        base[: max(0, limit - len(suffix))] + suffix
                    )[:limit]
                else:
                    candidate = next_value(
                        column, values.get(target), index, counter, self.plan.seed
                    )
                    if candidate is None:
                        break
                signature = tuple(
                    _freeze(candidate if name == target else values.get(name))
                    for name in key_columns
                )
                if signature not in seen or counter > 100:
                    break
                counter += 1
            if candidate is None:
                continue
            values[target] = candidate
            seen.add(signature)

        return tuple(values.get(name) for name in names)
