"""Dependency-safe creation and insertion order.

A dump is a bag of statements. Replaying it or copying the schema needs an
order that respects dependencies:

1. extensions
2. enum types
3. composite / range types
4. domains
5. sequences
6. tables in foreign-key order
7. functions and procedures
8. indexes
9. triggers
10. views and materialized views (in view-dependency order)
11. rules
12. policies
13. comments and grants

The order is derived from the catalog, not from the dump's statement order, so
it also works when the dump was produced by a tool that sorts alphabetically.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import generation_order
from .ir import Catalog, SqlObject


@dataclass
class CreationStep:
    phase: str
    kind: str
    name: str
    depends_on: list[str] = field(default_factory=list)
    provenance: str | None = None
    cycle: bool = False


@dataclass
class CreationOrder:
    steps: list[CreationStep]
    cycles: list[list[str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _object_name(obj: SqlObject) -> str:
    if obj.schema and obj.name:
        return f"{obj.schema}.{obj.name}"
    return obj.name or "(anonymous)"


def _provenance(obj: object) -> str | None:
    provenance = getattr(obj, "provenance", None)
    return getattr(provenance, "source", None)


def _resolve_table(catalog: Catalog, name: str, default_schema: str) -> str | None:
    parts = name.split(".")
    if len(parts) >= 2:
        key = f"{parts[-2]}.{parts[-1]}"
        return key if key in catalog.table_keys else None
    for key in (f"{default_schema}.{name}", f"public.{name}"):
        if key in catalog.table_keys:
            return key
    for key in catalog.table_keys:
        if key.split(".")[-1] == name:
            return key
    return None


def _resolve_function(catalog: Catalog, name: str, default_schema: str) -> str | None:
    parts = name.split(".")
    if len(parts) >= 2:
        key = f"{parts[-2]}.{parts[-1]}"
    else:
        key = f"{default_schema}.{name}"
    known = {f"{function.schema}.{function.name}" for function in catalog.functions}
    if key in known:
        return key
    for candidate in known:
        if candidate.split(".")[-1] == parts[-1]:
            return candidate
    return None


def _object_steps(
    objects: list[SqlObject], phase: str, kind: str, sort: bool = True
) -> list[CreationStep]:
    steps: list[CreationStep] = []
    ordered = sorted(objects, key=_object_name) if sort else objects
    for obj in ordered:
        steps.append(
            CreationStep(
                phase=phase,
                kind=kind,
                name=_object_name(obj),
                depends_on=list(obj.depends_on),
                provenance=_provenance(obj),
            )
        )
    return steps


def _order_view_group(
    objects: list[SqlObject],
) -> tuple[list[SqlObject], list[list[str]]]:
    by_name = {_object_name(obj): obj for obj in objects}
    dependencies = {
        name: [dependency for dependency in obj.depends_on if dependency in by_name]
        for name, obj in by_name.items()
    }
    remaining = set(by_name)
    order: list[str] = []
    cycles: list[list[str]] = []
    while remaining:
        ready = sorted(
            name for name in remaining if not (set(dependencies[name]) & remaining)
        )
        if not ready:
            cycle = sorted(remaining)
            cycles.append(cycle)
            ready = [cycle[0]]
        for name in ready:
            remaining.remove(name)
            order.append(name)
    return [by_name[name] for name in order], cycles


def creation_order(catalog: Catalog) -> CreationOrder:
    steps: list[CreationStep] = []
    cycles: list[list[str]] = []
    warnings: list[str] = []

    by_kind: dict[str, list[SqlObject]] = {}
    for obj in catalog.objects:
        by_kind.setdefault(obj.kind, []).append(obj)

    for phase, kind in (
        ("extension", "extension"),
        ("enum", "enum"),
        ("type", "type"),
        ("domain", "domain"),
        ("sequence", "sequence"),
    ):
        steps.extend(_object_steps(by_kind.get(kind, []), phase, kind))

    graph = generation_order(catalog)
    cycle_nodes = {node for cycle in graph.cycles for node in cycle}
    for key in graph.order:
        table = catalog.table(key)
        dependencies = sorted(
            {
                fk.parent_key
                for fk in table.foreign_keys
                if fk.parent_key != key and fk.parent_key in graph.edges
            }
        )
        steps.append(
            CreationStep(
                phase="table",
                kind="table",
                name=key,
                depends_on=dependencies,
                provenance=_provenance(table),
                cycle=key in cycle_nodes,
            )
        )
    for cycle in graph.cycles:
        cycles.append(cycle)
        warnings.append(
            "foreign key cycle: "
            + " -> ".join(cycle)
            + "; tables in the cycle are ordered together"
        )

    for function in sorted(
        catalog.functions, key=lambda item: f"{item.schema}.{item.name}"
    ):
        name = f"{function.schema}.{function.name}"
        dependencies: list[str] = []
        for target in [*function.reads, *function.writes]:
            key = _resolve_table(catalog, target, function.schema)
            if key and key not in dependencies:
                dependencies.append(key)
        steps.append(
            CreationStep(
                phase="function",
                kind=function.kind,
                name=name,
                depends_on=dependencies,
                provenance=_provenance(function),
            )
        )

    steps.extend(_object_steps(by_kind.get("index", []), "index", "index"))

    for table in sorted(catalog.tables, key=lambda item: item.key):
        for trigger in sorted(table.triggers, key=lambda item: item.name):
            dependencies = [table.key]
            function_key = _resolve_function(catalog, trigger.function, table.schema)
            if function_key is not None:
                dependencies.append(function_key)
            steps.append(
                CreationStep(
                    phase="trigger",
                    kind="trigger",
                    name=f"{table.key}.{trigger.name}",
                    depends_on=dependencies,
                    provenance=_provenance(trigger),
                )
            )

    for phase, kind in (("view", "view"), ("materialized_view", "materialized_view")):
        objects, view_cycles = _order_view_group(by_kind.get(kind, []))
        steps.extend(_object_steps(objects, phase, kind, sort=False))
        for cycle in view_cycles:
            cycles.append(cycle)
            warnings.append(
                "view dependency cycle: " + " -> ".join(cycle) + "; broken in name order"
            )

    steps.extend(_object_steps(by_kind.get("rule", []), "rule", "rule"))
    steps.extend(_object_steps(by_kind.get("policy", []), "policy", "policy"))
    steps.extend(_object_steps(by_kind.get("comment", []), "comment", "comment"))
    steps.extend(_object_steps(by_kind.get("grant", []), "grant", "grant"))

    known_kinds = {
        "extension",
        "enum",
        "type",
        "domain",
        "sequence",
        "index",
        "rule",
        "policy",
        "comment",
        "grant",
        "view",
        "materialized_view",
    }
    for kind in sorted(by_kind):
        if kind in known_kinds:
            continue
        steps.extend(_object_steps(by_kind[kind], kind, kind))

    return CreationOrder(steps=steps, cycles=cycles, warnings=warnings)
