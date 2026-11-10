"""Catalog filters: schema, include and exclude.

Filters are applied after introspection, so they work with a live database and
with a saved catalog. Patterns are shell globs matched against `schema.table`
and against the bare table name.
"""

from __future__ import annotations

from fnmatch import fnmatch

from .ir import Catalog


def split_patterns(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _matches(pattern: str, schema: str, name: str) -> bool:
    return fnmatch(f"{schema}.{name}", pattern) or fnmatch(name, pattern)


def filter_catalog(
    catalog: Catalog,
    schemas: list[str] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> Catalog:
    schema_set = {schema.strip() for schema in (schemas or []) if schema.strip()}
    include = [pattern for pattern in (include or []) if pattern]
    exclude = [pattern for pattern in (exclude or []) if pattern]

    tables = []
    for table in catalog.tables:
        if schema_set and table.schema not in schema_set:
            continue
        if include and not any(_matches(pattern, table.schema, table.name) for pattern in include):
            continue
        if any(_matches(pattern, table.schema, table.name) for pattern in exclude):
            continue
        tables.append(table)

    return Catalog(
        database=catalog.database,
        server_version=catalog.server_version,
        tables=tables,
        schemas=sorted({table.schema for table in tables}),
        functions=catalog.functions,
        objects=catalog.objects,
        rules=catalog.rules,
    )
