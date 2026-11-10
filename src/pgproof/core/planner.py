"""Generation plan: how many rows per table and in what order."""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import generation_order
from .ir import Catalog, all_partition_columns


class PlanError(RuntimeError):
    pass


@dataclass
class TablePlan:
    key: str
    rows: int
    parents: list[str] = field(default_factory=list)


@dataclass
class Plan:
    seed: int
    order: list[str]
    tables: dict[str, TablePlan]
    cycles: list[list[str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(plan.rows for plan in self.tables.values())


def build_plan(
    catalog: Catalog,
    rows: int = 1000,
    table_rows: dict[str, int] | None = None,
    seed: int = 42,
) -> Plan:
    if rows < 0:
        raise PlanError("rows must be >= 0")
    overrides = dict(table_rows or {})
    known = set(catalog.table_keys)
    for key in overrides:
        if key not in known:
            raise PlanError(f"unknown table in --table-rows: {key!r}")

    graph = generation_order(catalog)
    tables: dict[str, TablePlan] = {}
    for key in catalog.table_keys:
        table = catalog.table(key)
        parents = sorted(
            {
                fk.parent_key
                for fk in table.foreign_keys
                if fk.parent_key != key and fk.parent_key in known
            }
        )
        tables[key] = TablePlan(key=key, rows=overrides.get(key, rows), parents=parents)

    warnings: list[str] = []
    for cycle in graph.cycles:
        warnings.append(
            "foreign key cycle detected: "
            + " -> ".join(cycle)
            + "; primary keys are pre-allocated so the cycle can still be generated"
        )
    for key, plan in tables.items():
        table = catalog.table(key)
        if plan.rows and not table.primary_key:
            unique_keys = [u for u in table.uniques]
            if not unique_keys:
                warnings.append(
                    f"{key} has no primary key or unique constraint; "
                    "rows cannot be referenced by foreign keys"
                )
        if table.is_partitioned:
            if not any(
                partition.strategy in {"range", "list"}
                for partition in table.partitions
            ):
                warnings.append(
                    f"{key}: no range or list partitions found; "
                    "the partition key is generated without bounds"
                )
            all_columns = all_partition_columns(table)
            if set(table.primary_key) == set(all_columns) and len(all_columns) > 1:
                warnings.append(
                    f"{key}: partition-key-only primary key with multiple columns; "
                    "uniqueness is not guaranteed"
                )

    return Plan(
        seed=seed,
        order=graph.order,
        tables=tables,
        cycles=graph.cycles,
        warnings=warnings,
    )


def buckets(plan: Plan, batch_size: int) -> list[list[str]]:
    """Group tables into batches for progress reporting."""
    keys = plan.order
    return [keys[index : index + batch_size] for index in range(0, len(keys), batch_size)]
