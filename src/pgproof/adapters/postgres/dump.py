"""Load a PostgreSQL schema dump into the domain Catalog.

The dump is the source of truth when there is no live database: tables,
columns, constraints, enums, domains, partitions, functions, triggers, views
and indexes. Generation uses the same Catalog as the live introspection path,
so relationships, checks and partitions behave identically.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from ...core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    DataKind,
    ForeignKey,
    Function,
    Partition,
    Table,
    Trigger,
    UniqueConstraint,
)
from .introspect import parse_partition_bound

_IDENT = r'(?:"[^"]+"|[\w$]+)'
_QUALIFIED = rf"{_IDENT}(?:\.{_IDENT})?"
_WS = r"\s+"
logger = logging.getLogger(__name__)


def _unquote(name: str) -> str:
    name = name.strip()
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('""', '"')
    return name


def _split_qualified(name: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for char in name:
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
        elif char == "." and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [_unquote(part) for part in parts]


def split_statements(sql: str) -> list[str]:
    """Split a PostgreSQL dump into statements without breaking dollar quotes."""
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(sql)
    in_single = in_double = in_line = in_block = False
    dollar_tag: str | None = None
    line_start = True

    while index < length:
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < length else ""

        if in_line:
            if char == "\n":
                in_line = False
                buffer.append(char)
                line_start = True
            index += 1
            continue

        if in_block:
            if char == "*" and nxt == "/":
                in_block = False
                index += 2
                continue
            if char == "\n":
                line_start = True
            index += 1
            continue

        if dollar_tag:
            if sql.startswith(dollar_tag, index):
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            buffer.append(char)
            line_start = char == "\n"
            index += 1
            continue

        if in_single:
            buffer.append(char)
            if char == "'" and nxt == "'":
                buffer.append(nxt)
                index += 2
                continue
            if char == "'":
                in_single = False
            line_start = char == "\n"
            index += 1
            continue

        if in_double:
            buffer.append(char)
            if char == '"' and nxt == '"':
                buffer.append(nxt)
                index += 2
                continue
            if char == '"':
                in_double = False
            index += 1
            continue

        if line_start and char == "\\":
            while index < length and sql[index] != "\n":
                index += 1
            line_start = True
            continue

        if char == "-" and nxt == "-":
            in_line = True
            index += 2
            continue
        if char == "/" and nxt == "*":
            in_block = True
            index += 2
            continue
        if char == "'":
            in_single = True
            buffer.append(char)
            index += 1
            continue
        if char == '"':
            in_double = True
            buffer.append(char)
            index += 1
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", sql[index:])
            if match:
                dollar_tag = match.group(0)
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            if statement.lstrip().upper().startswith("COPY ") and "FROM STDIN" in statement.upper():
                while index < length:
                    end = sql.find("\n", index)
                    line = sql[index : end if end != -1 else length].strip()
                    index = (end + 1) if end != -1 else length
                    if line == "\\.":
                        break
            line_start = True
            continue

        buffer.append(char)
        line_start = char == "\n"
        index += 1

    statement = "".join(buffer).strip()
    if statement:
        statements.append(statement)
    return statements


class DumpLoader:
    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}
        self.enums: dict[str, list[str]] = {}
        self.domains: dict[str, tuple[str, str | None]] = {}
        self.functions: list[Function] = []
        self.function_by_name: dict[str, Function] = {}
        self.trigger_specs: list[dict[str, Any]] = []
        self.views: list[str] = []
        self.partition_relations: list[dict[str, Any]] = []
        self.partition_specs: dict[str, tuple[str, list[str]]] = {}
        self.search_path: list[str] = ["public"]
        self.schemas: set[str] = {"public"}
        self.database = "dump"

    # ------------------------------------------------------------------ entry
    def load(self, sql: str) -> Catalog:
        for statement in split_statements(sql):
            try:
                self._statement(statement)
            except Exception as exc:  # noqa: BLE001 - one bad statement must not drop the dump
                logger.warning("skipping unparseable statement: %s", exc)
                continue
        self._attach_triggers()
        self._build_partitions()
        self._infer_relationships()
        return Catalog(
            database=self.database,
            server_version="dump",
            tables=sorted(self.tables.values(), key=lambda table: table.key),
            schemas=sorted({table.schema for table in self.tables.values()}),
            functions=self.functions,
        )

    def _statement(self, statement: str) -> None:
        head = statement.lstrip().upper()
        if head.startswith("SET "):
            self._set(statement)
        elif head.startswith("CREATE SCHEMA"):
            self._create_schema(statement)
        elif head.startswith("CREATE TYPE"):
            self._create_type(statement)
        elif head.startswith("CREATE DOMAIN"):
            self._create_domain(statement)
        elif head.startswith("CREATE TABLE"):
            self._create_table(statement)
        elif head.startswith("ALTER TABLE"):
            self._alter_table(statement)
        elif head.startswith(("CREATE UNIQUE INDEX", "CREATE INDEX")):
            self._create_index(statement)
        elif head.startswith(
            (
                "CREATE OR REPLACE FUNCTION",
                "CREATE FUNCTION",
                "CREATE OR REPLACE PROCEDURE",
                "CREATE PROCEDURE",
            )
        ):
            self._create_function(statement)
        elif head.startswith(("CREATE TRIGGER", "CREATE OR REPLACE TRIGGER")):
            self._create_trigger(statement)
        elif head.startswith(("CREATE VIEW", "CREATE MATERIALIZED VIEW")):
            self.views.append(statement)

    # ----------------------------------------------------------------- basics
    def _qualified(self, name: str, default_schema: str | None = None) -> tuple[str, str]:
        parts = _split_qualified(name)
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return default_schema or self.search_path[0], parts[-1]

    def _table_key(self, schema: str, name: str) -> str:
        return f"{schema}.{name}"

    def _set(self, statement: str) -> None:
        match = re.search(
            rf"SET{_WS}search_path{_WS}(?:=|TO){_WS}(.+)$", statement, re.IGNORECASE | re.DOTALL
        )
        if not match:
            return
        raw = match.group(1)
        paths = []
        for item in raw.split(","):
            name = _unquote(item.strip())
            if name and name not in {"pg_catalog", "$user"}:
                paths.append(name)
        if paths:
            self.search_path = paths

    def _create_schema(self, statement: str) -> None:
        match = re.search(rf"CREATE{_WS}SCHEMA{_WS}(?:IF{_WS}NOT{_WS}EXISTS{_WS})?({_IDENT})", statement, re.IGNORECASE)
        if match:
            self.schemas.add(_unquote(match.group(1)))

    def _create_type(self, statement: str) -> None:
        match = re.search(
            rf"CREATE{_WS}TYPE{_WS}({_QUALIFIED}){_WS}AS{_WS}ENUM{_WS}\((.*)\)",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return
        schema, name = self._qualified(match.group(1))
        values = [
            value.replace("''", "'")
            for value in re.findall(r"'((?:[^']|'')*)'", match.group(2))
        ]
        self.enums[f"{schema}.{name}"] = values
        self.enums.setdefault(name, values)

    def _create_domain(self, statement: str) -> None:
        match = re.match(
            rf"CREATE{_WS}DOMAIN{_WS}({_QUALIFIED}){_WS}AS{_WS}(.+?)"
            rf"(?:{_WS}CHECK{_WS}\((.*)\))?\s*$",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return
        schema, name = self._qualified(match.group(1))
        base_type = " ".join(match.group(2).split())
        check = match.group(3).strip() if match.group(3) else None
        self.domains[f"{schema}.{name}"] = (base_type, check)
        self.domains.setdefault(name, (base_type, check))

    def _enum_values(self, sql_type: str) -> list[str]:
        name = _unquote(sql_type.strip())
        if name in self.enums:
            return self.enums[name]
        unqualified = name.split(".")[-1]
        for key, values in self.enums.items():
            if key.split(".")[-1] == unqualified:
                return values
        return []

    def _domain(self, sql_type: str) -> tuple[str, str | None] | None:
        name = _unquote(sql_type.strip())
        if name in self.domains:
            return self.domains[name]
        unqualified = name.split(".")[-1]
        for key, value in self.domains.items():
            if key.split(".")[-1] == unqualified:
                return value
        return None

    # ---------------------------------------------------------------- tables
    def _create_table(self, statement: str) -> None:
        if re.search(rf"PARTITION{_WS}OF", statement, re.IGNORECASE):
            self._create_partition(statement)
            return

        tree = sqlglot.parse_one(statement, read="postgres")
        if not isinstance(tree, exp.Create) or tree.kind != "TABLE":
            return
        schema_expr = tree.this
        if not isinstance(schema_expr, exp.Schema):
            return
        table_expr = schema_expr.this
        if not isinstance(table_expr, exp.Table):
            return
        schema = table_expr.db or self.search_path[0]
        name = table_expr.name
        key = self._table_key(schema, name)
        table = Table(schema=schema, name=name)

        partition_spec = self._partition_spec(statement)
        if partition_spec:
            strategy, columns = partition_spec
            table.is_partitioned = True
            table.partition_strategy = strategy
            table.partition_key = columns
            self.partition_specs[key] = (strategy, columns)

        for node in schema_expr.expressions:
            if isinstance(node, exp.ColumnDef):
                self._add_column(table, node)
            elif isinstance(node, exp.Constraint):
                for sub in node.expressions:
                    self._apply_constraint(table, node.name, sub)
            elif isinstance(node, (exp.PrimaryKey, exp.ForeignKey, exp.UniqueColumnConstraint, exp.CheckColumnConstraint)):
                self._apply_constraint(table, None, node)

        self.tables[key] = table

    def _add_column(self, table: Table, node: Any) -> None:
        name = node.name
        kind = node.args.get("kind")
        sql_type = kind.sql(dialect="postgres") if kind is not None else "text"
        enum_values = self._enum_values(sql_type)
        domain = self._domain(sql_type)
        domain_check = None
        if domain is not None:
            base_type, domain_check = domain
            sql_type = base_type
        column = Column(
            name=name,
            sql_type=sql_type,
            nullable=True,
            enum_values=enum_values,
        )
        for constraint in node.args.get("constraints") or []:
            constraint_kind = constraint.args.get("kind")
            if isinstance(constraint_kind, exp.NotNullColumnConstraint):
                column.nullable = False
            elif isinstance(constraint_kind, exp.PrimaryKeyColumnConstraint):
                column.nullable = False
                if name not in table.primary_key:
                    table.primary_key.append(name)
            elif isinstance(constraint_kind, exp.DefaultColumnConstraint):
                column.default = constraint_kind.this.sql(dialect="postgres")
            elif isinstance(constraint_kind, exp.GeneratedAsIdentityColumnConstraint):
                column.identity = "a" if constraint_kind.args.get("this") else "d"
            elif isinstance(constraint_kind, exp.ComputedColumnConstraint):
                column.generated = "s"
            elif isinstance(constraint_kind, exp.UniqueColumnConstraint):
                table.uniques.append(
                    UniqueConstraint(
                        name=f"{table.name}_{name}_key",
                        columns=[name],
                    )
                )
            elif isinstance(constraint_kind, exp.Reference):
                self._add_foreign_key(
                    table,
                    f"{table.name}_{name}_fkey",
                    [name],
                    constraint_kind,
                )
            elif isinstance(constraint_kind, exp.CheckColumnConstraint):
                predicate = constraint_kind.this.sql(dialect="postgres")
                table.checks.append(
                    CheckConstraint(
                        name=f"{table.name}_{name}_check",
                        definition=f"CHECK ({predicate})",
                        predicate=predicate,
                    )
                )
        table.columns.append(column)
        if domain_check:
            predicate = re.sub(r"\bVALUE\b", name, domain_check, flags=re.IGNORECASE)
            table.checks.append(
                CheckConstraint(
                    name=f"{table.name}_{name}_domain",
                    definition=f"CHECK ({predicate})",
                    predicate=predicate,
                )
            )

    def _apply_constraint(self, table: Table, name: str | None, node: Any) -> None:
        if isinstance(node, exp.PrimaryKey):
            table.primary_key = [item.name for item in node.expressions]
            for column in table.primary_key:
                try:
                    table.column(column).nullable = False
                except KeyError:
                    pass
        elif isinstance(node, exp.UniqueColumnConstraint):
            schema = node.this
            if isinstance(schema, exp.Schema):
                columns = [item.name for item in schema.expressions]
            else:
                columns = [schema.name]
            table.uniques.append(
                UniqueConstraint(
                    name=name or f"{table.name}_{columns[0]}_key",
                    columns=columns,
                )
            )
        elif isinstance(node, exp.CheckColumnConstraint):
            predicate = node.this.sql(dialect="postgres")
            table.checks.append(
                CheckConstraint(
                    name=name or f"{table.name}_check_{len(table.checks) + 1}",
                    definition=f"CHECK ({predicate})",
                    predicate=predicate,
                )
            )
        elif isinstance(node, exp.ForeignKey):
            columns = [item.name for item in node.expressions]
            reference = node.args.get("reference")
            if reference is not None:
                self._add_foreign_key(
                    table,
                    name or f"{table.name}_{columns[0]}_fkey",
                    columns,
                    reference,
                )

    def _add_foreign_key(
        self,
        table: Table,
        name: str,
        child_columns: list[str],
        reference: Any,
    ) -> None:
        ref_schema = reference.this
        if not isinstance(ref_schema, exp.Schema):
            return
        parent_expr = ref_schema.this
        if not isinstance(parent_expr, exp.Table):
            return
        parent_schema = parent_expr.db or table.schema
        parent_columns = [item.name for item in ref_schema.expressions]
        on_delete = None
        for option in reference.args.get("options") or []:
            sql = option if isinstance(option, str) else option.sql(dialect="postgres")
            match = re.search(r"ON\s+DELETE\s+(\w+)", sql, re.IGNORECASE)
            if match:
                on_delete = match.group(1)
        table.foreign_keys.append(
            ForeignKey(
                name=name,
                child_schema=table.schema,
                child_table=table.name,
                child_columns=child_columns,
                parent_schema=parent_schema,
                parent_table=parent_expr.name,
                parent_columns=parent_columns,
                on_delete=on_delete,
            )
        )

    def _alter_table(self, statement: str) -> None:
        tree = sqlglot.parse_one(statement, read="postgres")
        if not isinstance(tree, exp.Alter):
            return
        table_expr = tree.this
        if not isinstance(table_expr, exp.Table):
            return
        schema = table_expr.db or self.search_path[0]
        table = self.tables.get(self._table_key(schema, table_expr.name))
        if table is None:
            return
        for action in tree.args.get("actions") or []:
            if isinstance(action, exp.AddConstraint):
                for node in action.expressions:
                    if isinstance(node, exp.Constraint):
                        for sub in node.expressions:
                            self._apply_constraint(table, node.name, sub)
                    else:
                        self._apply_constraint(table, None, node)
            elif isinstance(action, exp.AlterColumn):
                name = action.this.name if action.this is not None else None
                if not name:
                    continue
                try:
                    column = table.column(name)
                except KeyError:
                    continue
                default = action.args.get("default")
                if default is not None:
                    column.default = default.sql(dialect="postgres")

    def _create_index(self, statement: str) -> None:
        tree = sqlglot.parse_one(statement, read="postgres")
        if not isinstance(tree, exp.Create) or tree.kind != "INDEX":
            return
        if not tree.args.get("unique"):
            return
        index = tree.this
        if not isinstance(index, exp.Index):
            return
        table_expr = index.args.get("table")
        if not isinstance(table_expr, exp.Table):
            return
        schema = table_expr.db or self.search_path[0]
        table = self.tables.get(self._table_key(schema, table_expr.name))
        if table is None:
            return
        params = index.args.get("params")
        columns = []
        for item in getattr(params, "args", {}).get("columns", []) if params else []:
            target = item.this if isinstance(item, exp.Ordered) else item
            if isinstance(target, exp.Column):
                columns.append(target.name)
            else:
                return
        if columns:
            table.uniques.append(
                UniqueConstraint(
                    name=index.name or f"{table.name}_{columns[0]}_key",
                    columns=columns,
                )
            )

    # ---------------------------------------------------------- partitions
    def _partition_spec(self, statement: str) -> tuple[str, list[str]] | None:
        match = re.search(
            rf"PARTITION{_WS}BY{_WS}(RANGE|LIST|HASH){_WS}\((.*?)\)",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        strategy = match.group(1).lower()
        columns = [
            _unquote(item.strip())
            for item in match.group(2).split(",")
            if item.strip()
        ]
        return strategy, columns

    def _create_partition(self, statement: str) -> None:
        match = re.match(
            rf"CREATE{_WS}TABLE{_WS}(?:IF{_WS}NOT{_WS}EXISTS{_WS})?({_QUALIFIED}){_WS}PARTITION{_WS}OF{_WS}({_QUALIFIED})",
            statement,
            re.IGNORECASE,
        )
        if not match:
            return
        child_schema, child_name = self._qualified(match.group(1))
        parent_schema, parent_name = self._qualified(match.group(2), child_schema)
        bound_match = re.search(
            rf"(FOR{_WS}VALUES{_WS}.*?|DEFAULT)(?={_WS}PARTITION{_WS}BY|\s*$)",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        bound = bound_match.group(1).strip() if bound_match else "DEFAULT"
        spec = self._partition_spec(statement)
        if spec:
            self.partition_specs[self._table_key(child_schema, child_name)] = spec
        self.partition_relations.append(
            {
                "parent": self._table_key(parent_schema, parent_name),
                "child_schema": child_schema,
                "child_name": child_name,
                "bound": bound,
            }
        )

    def _build_partitions(self) -> None:
        if not self.partition_relations:
            return
        for key, (strategy, columns) in self.partition_specs.items():
            table = self.tables.get(key)
            if table is not None:
                table.is_partitioned = True
                table.partition_strategy = strategy
                table.partition_key = columns

        parent_of = {relation["child_key"]: relation["parent"] for relation in self.partition_relations if "child_key" in relation}
        for relation in self.partition_relations:
            parent_of[self._table_key(relation["child_schema"], relation["child_name"])] = relation["parent"]

        def root_of(key: str) -> str | None:
            seen: set[str] = set()
            current = key
            while current not in self.tables:
                if current in seen or current not in parent_of:
                    return None
                seen.add(current)
                current = parent_of[current]
            return current

        nodes: dict[str, Partition] = {}
        for relation in self.partition_relations:
            parent_key = relation["parent"]
            child_key = self._table_key(relation["child_schema"], relation["child_name"])
            _strategy, key_columns = self.partition_specs.get(parent_key, ("", []))
            root = root_of(parent_key)
            columns = []
            if root:
                root_table = self.tables[root]
                for name in key_columns:
                    try:
                        columns.append(root_table.column(name))
                    except KeyError:
                        columns.append(None)
            node = parse_partition_bound(relation["bound"], columns)
            if node is None:
                continue
            node.schema = relation["child_schema"]
            node.name = relation["child_name"]
            node.columns = key_columns
            nodes[child_key] = node
            if root and child_key in self.partition_specs:
                self.tables[root].has_sub_partitions = True

        for child_key, node in nodes.items():
            parent_key = parent_of.get(child_key)
            if parent_key is None:
                continue
            if parent_key in self.tables:
                self.tables[parent_key].partitions.append(node)
            elif parent_key in nodes:
                nodes[parent_key].children.append(node)

    # ------------------------------------------------------ functions/triggers
    def _create_function(self, statement: str) -> None:
        head = re.match(
            rf"CREATE{_WS}(?:OR{_WS}REPLACE{_WS})?(FUNCTION|PROCEDURE){_WS}({_QUALIFIED})",
            statement,
            re.IGNORECASE,
        )
        if not head:
            return
        schema, name = self._qualified(head.group(2))
        body_match = re.search(r"\bAS\s+(\$[A-Za-z_0-9]*\$)(.*?)\1", statement, re.DOTALL)
        body = body_match.group(2).strip() if body_match else ""
        function = Function(
            oid=len(self.functions) + 1,
            schema=schema,
            name=name,
            body=body,
        )
        self.functions.append(function)
        self.function_by_name[f"{schema}.{name}"] = function
        self.function_by_name.setdefault(name, function)

    def _create_trigger(self, statement: str) -> None:
        match = re.match(
            rf"CREATE{_WS}(?:OR{_WS}REPLACE{_WS})?(?:CONSTRAINT{_WS})?TRIGGER{_WS}({_QUALIFIED}){_WS}"
            rf"(BEFORE|AFTER|INSTEAD{_WS}OF){_WS}(.+?){_WS}ON{_WS}({_QUALIFIED})(.*?)"
            rf"EXECUTE{_WS}(?:FUNCTION|PROCEDURE){_WS}({_QUALIFIED})",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return
        _name_schema, name = self._qualified(match.group(1))
        timing = " ".join(match.group(2).split())
        events = " ".join(match.group(3).split())
        table_schema, table_name = self._qualified(match.group(4))
        function_schema, function_name = self._qualified(match.group(6))
        self.trigger_specs.append(
            {
                "name": name,
                "timing": timing,
                "events": events,
                "table_key": self._table_key(table_schema, table_name),
                "function_name": function_name,
                "function_key": f"{function_schema}.{function_name}",
                "definition": statement,
            }
        )

    def _attach_triggers(self) -> None:
        for spec in self.trigger_specs:
            table = self.tables.get(spec["table_key"])
            if table is None:
                continue
            function = self.function_by_name.get(spec["function_key"]) or self.function_by_name.get(
                spec["function_name"]
            )
            table.triggers.append(
                Trigger(
                    name=spec["name"],
                    timing=spec["timing"],
                    events=spec["events"],
                    function=spec["function_name"],
                    definition=spec["definition"],
                    function_oid=function.oid if function else None,
                )
            )

    # --------------------------------------------------------- relationships
    def _infer_relationships(self) -> None:
        for view in self.views:
            self._infer_from_view(view)
        for table in list(self.tables.values()):
            for column in table.columns:
                if not column.name.endswith("_id"):
                    continue
                if any(column.name in fk.child_columns for fk in table.foreign_keys):
                    continue
                parent = self._guess_parent(table, column)
                if parent is None:
                    continue
                parent_table, parent_column = parent
                table.foreign_keys.append(
                    ForeignKey(
                        name=f"{table.name}_{column.name}_fkey",
                        child_schema=table.schema,
                        child_table=table.name,
                        child_columns=[column.name],
                        parent_schema=parent_table.schema,
                        parent_table=parent_table.name,
                        parent_columns=[parent_column],
                        inferred=True,
                    )
                )

    def _guess_parent(self, table: Table, column: Column) -> tuple[Table, str] | None:
        base = column.name[: -len("_id")]
        candidates = {base, f"{base}s", f"{base}es"}
        if base.endswith("y"):
            candidates.add(f"{base[:-1]}ies")
        for candidate in self.tables.values():
            if candidate.name not in candidates:
                continue
            if len(candidate.primary_key) != 1:
                continue
            parent_column = candidate.primary_key[0]
            try:
                parent = candidate.column(parent_column)
            except KeyError:
                continue
            if _compatible(column, parent):
                return candidate, parent_column
        return None

    def _infer_from_view(self, statement: str) -> None:
        pattern = re.compile(
            rf"JOIN{_WS}({_QUALIFIED}){_WS}(?:{_IDENT}{_WS})?ON{_WS}"
            rf"({_QUALIFIED})\.({_IDENT}){_WS}={_WS}({_QUALIFIED})\.({_IDENT})",
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(statement):
            left_schema, left_table = self._qualified(match.group(2))
            right_schema, right_table = self._qualified(match.group(4))
            left = self.tables.get(self._table_key(left_schema, left_table))
            right = self.tables.get(self._table_key(right_schema, right_table))
            if left is None or right is None:
                continue
            left_column = _unquote(match.group(3))
            right_column = _unquote(match.group(5))
            if left_column in left.primary_key and right_column not in right.primary_key:
                self._add_inferred_fk(right, right_column, left, left_column)
            elif right_column in right.primary_key and left_column not in left.primary_key:
                self._add_inferred_fk(left, left_column, right, right_column)

    def _add_inferred_fk(
        self, child: Table, child_column: str, parent: Table, parent_column: str
    ) -> None:
        if any(child_column in fk.child_columns for fk in child.foreign_keys):
            return
        try:
            child_type = child.column(child_column)
            parent_type = parent.column(parent_column)
        except KeyError:
            return
        if not _compatible(child_type, parent_type):
            return
        child.foreign_keys.append(
            ForeignKey(
                name=f"{child.name}_{child_column}_fkey",
                child_schema=child.schema,
                child_table=child.name,
                child_columns=[child_column],
                parent_schema=parent.schema,
                parent_table=parent.name,
                parent_columns=[parent_column],
                inferred=True,
            )
        )


def _compatible(left: Column, right: Column) -> bool:
    if left.data_kind == right.data_kind:
        return True
    integer_kinds = {DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT}
    if left.data_kind in integer_kinds and right.data_kind in integer_kinds:
        return True
    text_kinds = {DataKind.TEXT, DataKind.ENUM}
    return bool(left.data_kind in text_kinds and right.data_kind in text_kinds)


def load_dump(path: str | Path) -> Catalog:
    sql = Path(path).read_text(encoding="utf-8")
    return DumpLoader().load(sql)
