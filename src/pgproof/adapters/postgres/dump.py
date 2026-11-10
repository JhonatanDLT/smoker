"""Load a PostgreSQL schema dump into the domain Catalog.

The dump is the source of truth when there is no live database: tables,
columns, constraints, enums, domains, partitions, functions, triggers, views,
rules, policies, sequences and indexes. Generation uses the same Catalog as the
live introspection path, so relationships, checks and partitions behave
identically.

Every extracted object carries a ``Provenance`` so the caller can decide the
creation and insertion order later.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from ...core.ir import (
    Catalog,
    CheckConstraint,
    Column,
    DataKind,
    ExclusionConstraint,
    ExclusionElement,
    ForeignKey,
    Function,
    Partition,
    Provenance,
    RuleCandidate,
    SqlObject,
    Table,
    Trigger,
    UniqueConstraint,
)
from .introspect import parse_partition_bound

_IDENT = r'(?:"[^"]+"|[\w$]+)'
_QUALIFIED = rf"{_IDENT}(?:\.{_IDENT})?"
_WS = r"\s+"
_EXCLUDE = getattr(exp, "ExcludeColumnConstraint", None)
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


def _statement_head(statement: str) -> str:
    return statement.lstrip().upper()


class DumpLoader:
    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}
        self.enums: dict[str, list[str]] = {}
        self.domains: dict[str, tuple[str, str | None]] = {}
        self.functions: list[Function] = []
        self.function_by_name: dict[str, Function] = {}
        self.trigger_specs: list[dict[str, Any]] = []
        self.views: list[str] = []
        self.objects: list[SqlObject] = []
        self.rules: list[RuleCandidate] = []
        self._rule_keys: set[tuple[str, str, str]] = set()
        self.partition_relations: list[dict[str, Any]] = []
        self.attach_relations: list[dict[str, Any]] = []
        self.partition_specs: dict[str, tuple[str, list[str]]] = {}
        self.like_specs: list[dict[str, Any]] = []
        self.inherits_specs: list[dict[str, Any]] = []
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

        self._resolve_like_and_inherits()
        self._attach_triggers()
        self._build_partitions()
        self._infer_relationships()
        self._analyze_functions()
        self._extract_rules()
        return Catalog(
            database=self.database,
            server_version="dump",
            tables=sorted(self.tables.values(), key=lambda table: table.key),
            schemas=sorted({table.schema for table in self.tables.values()}),
            functions=self.functions,
            objects=self.objects,
            rules=self.rules,
        )

    def _statement(self, statement: str) -> None:
        head = _statement_head(statement)
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
        elif head.startswith("CREATE MATERIALIZED VIEW"):
            self._create_view(statement, materialized=True)
        elif head.startswith(("CREATE VIEW", "CREATE OR REPLACE VIEW")):
            self._create_view(statement)
        elif head.startswith("CREATE RULE"):
            self._store_object("rule", statement)
        elif head.startswith("CREATE POLICY"):
            self._store_object("policy", statement)
        elif head.startswith("CREATE SEQUENCE"):
            self._store_object("sequence", statement)
        elif head.startswith("CREATE EXTENSION"):
            self._store_object("extension", statement)
        elif head.startswith("CREATE FOREIGN TABLE"):
            self._store_object("foreign_table", statement)
        elif head.startswith("ALTER SEQUENCE"):
            self._store_object("sequence_alter", statement)
        elif head.startswith("COMMENT ON"):
            self._store_object("comment", statement)
        elif head.startswith(("GRANT", "REVOKE")):
            self._store_object("grant", statement)

    def _prov(self, kind: str, source: str, detail: str | None = None) -> Provenance:
        first = source.splitlines()[0].strip() if source else ""
        return Provenance(kind=kind, source=first[:200], detail=detail)

    def _store_object(self, kind: str, statement: str) -> None:
        patterns = {
            "view": rf"CREATE{_WS}(?:OR{_WS}REPLACE{_WS})?(?:MATERIALIZED{_WS})?VIEW{_WS}({_QUALIFIED})",
            "materialized_view": rf"CREATE{_WS}MATERIALIZED{_WS}VIEW{_WS}({_QUALIFIED})",
            "rule": rf"CREATE{_WS}(?:OR{_WS}REPLACE{_WS})?RULE{_WS}({_QUALIFIED})",
            "policy": rf"CREATE{_WS}POLICY{_WS}({_QUALIFIED})",
            "sequence": rf"CREATE{_WS}SEQUENCE{_WS}(?:IF{_WS}NOT{_WS}EXISTS{_WS})?({_QUALIFIED})",
            "extension": rf"CREATE{_WS}EXTENSION{_WS}(?:IF{_WS}NOT{_WS}EXISTS{_WS})?({_IDENT})",
            "type": rf"CREATE{_WS}TYPE{_WS}({_QUALIFIED})",
            "enum": rf"CREATE{_WS}TYPE{_WS}({_QUALIFIED}){_WS}AS{_WS}ENUM",
            "domain": rf"CREATE{_WS}DOMAIN{_WS}({_QUALIFIED})",
            "index": rf"CREATE{_WS}(?:UNIQUE{_WS})?INDEX{_WS}(?:CONCURRENTLY{_WS})?(?:IF{_WS}NOT{_WS}EXISTS{_WS})?({_QUALIFIED})",
            "foreign_table": rf"CREATE{_WS}FOREIGN{_WS}TABLE{_WS}({_QUALIFIED})",
        }
        schema = name = None
        pattern = patterns.get(kind)
        if pattern:
            match = re.search(pattern, statement, re.IGNORECASE | re.DOTALL)
            if match:
                if kind == "extension":
                    schema, name = None, _unquote(match.group(1))
                else:
                    schema, name = self._qualified(match.group(1))
        self.objects.append(
            SqlObject(
                kind=kind,
                schema=schema,
                name=name,
                definition=statement,
                provenance=self._prov("dump", statement, kind),
                depends_on=self._object_dependencies(kind, schema, name, statement),
            )
        )

    def _object_dependencies(
        self, kind: str, schema: str | None, name: str | None, statement: str
    ) -> list[str]:
        dependencies: list[str] = []
        if kind in {"view", "materialized_view"}:
            try:
                tree = sqlglot.parse_one(statement, read="postgres")
            except Exception as exc:  # noqa: BLE001 - a raw view is still stored
                logger.debug("cannot parse view dependencies: %s", exc)
                tree = None
            if tree is not None:
                own = self._table_key(schema, name) if schema and name else None
                for table in tree.find_all(exp.Table):
                    table_schema = table.db or self.search_path[0]
                    dependency = self._table_key(table_schema, table.name)
                    if dependency != own and dependency not in dependencies:
                        dependencies.append(dependency)
        elif kind in {"rule", "policy", "index"}:
            patterns = {
                "rule": rf"\bTO{_WS}({_QUALIFIED})",
                "policy": rf"\bON{_WS}({_QUALIFIED})",
                "index": rf"\bON{_WS}({_QUALIFIED})",
            }
            match = re.search(patterns[kind], statement, re.IGNORECASE)
            if match:
                dependency_schema, dependency_name = self._qualified(
                    match.group(1), schema
                )
                dependencies.append(
                    self._table_key(dependency_schema, dependency_name)
                )
        return dependencies

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
        match = re.search(
            rf"CREATE{_WS}SCHEMA{_WS}(?:IF{_WS}NOT{_WS}EXISTS{_WS})?({_IDENT})",
            statement,
            re.IGNORECASE,
        )
        if match:
            self.schemas.add(_unquote(match.group(1)))

    def _create_type(self, statement: str) -> None:
        enum_match = re.search(
            rf"CREATE{_WS}TYPE{_WS}({_QUALIFIED}){_WS}AS{_WS}ENUM{_WS}\((.*)\)",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if enum_match:
            schema, name = self._qualified(enum_match.group(1))
            values = [
                value.replace("''", "'")
                for value in re.findall(r"'((?:[^']|'')*)'", enum_match.group(2))
            ]
            self.enums[f"{schema}.{name}"] = values
            self.enums.setdefault(name, values)
            self._store_object("enum", statement)
            return
        self._store_object("type", statement)

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
        self._store_object("domain", statement)

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
            self._store_object("table", statement)
            return
        table_expr = schema_expr.this
        if not isinstance(table_expr, exp.Table):
            return
        schema = table_expr.db or self.search_path[0]
        name = table_expr.name
        key = self._table_key(schema, name)
        table = Table(
            schema=schema,
            name=name,
            provenance=self._prov("dump", statement, "table"),
        )

        partition_spec = self._partition_spec(statement)
        if partition_spec:
            strategy, columns = partition_spec
            table.is_partitioned = True
            table.partition_strategy = strategy
            table.partition_key = columns
            self.partition_specs[key] = (strategy, columns)

        for node in schema_expr.expressions:
            if isinstance(node, exp.ColumnDef):
                self._add_column(table, node, statement)
            elif isinstance(node, exp.LikeProperty):
                self._record_like(table, node, statement)
            elif isinstance(node, exp.Constraint):
                for sub in node.expressions:
                    self._apply_constraint(table, node.name, sub, statement)
            elif isinstance(
                node,
                (
                    exp.PrimaryKey,
                    exp.ForeignKey,
                    exp.UniqueColumnConstraint,
                    exp.CheckColumnConstraint,
                ),
            ) or (_EXCLUDE is not None and isinstance(node, _EXCLUDE)):
                self._apply_constraint(table, None, node, statement)

        for prop in tree.find_all(exp.InheritsProperty):
            for parent in prop.expressions:
                if isinstance(parent, exp.Table):
                    parent_schema = parent.db or schema
                    self.inherits_specs.append(
                        {
                            "child_key": key,
                            "parent_key": self._table_key(parent_schema, parent.name),
                            "statement": statement,
                        }
                    )

        self.tables[key] = table

    def _record_like(self, table: Table, node: Any, statement: str) -> None:
        parent = node.this
        if not isinstance(parent, exp.Table):
            return
        options: list[str] = []
        for prop in node.args.get("expressions") or []:
            value = prop.args.get("value")
            option = value.name if hasattr(value, "name") else str(value)
            options.append(option.upper())
        parent_schema = parent.db or table.schema
        self.like_specs.append(
            {
                "child_key": table.key,
                "parent_key": self._table_key(parent_schema, parent.name),
                "options": options,
                "statement": statement,
            }
        )

    def _add_column(self, table: Table, node: Any, statement: str) -> None:
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
                        provenance=self._prov("dump", statement, "unique"),
                    )
                )
            elif isinstance(constraint_kind, exp.Reference):
                self._add_foreign_key(
                    table,
                    f"{table.name}_{name}_fkey",
                    [name],
                    constraint_kind,
                    self._prov("dump", statement, "foreign_key"),
                )
            elif isinstance(constraint_kind, exp.CheckColumnConstraint):
                predicate = constraint_kind.this.sql(dialect="postgres")
                table.checks.append(
                    CheckConstraint(
                        name=f"{table.name}_{name}_check",
                        definition=f"CHECK ({predicate})",
                        predicate=predicate,
                        provenance=self._prov("dump", statement, "check"),
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
                    provenance=self._prov("dump", statement, "domain"),
                )
            )

    def _apply_constraint(
        self,
        table: Table,
        name: str | None,
        node: Any,
        statement: str,
        validated: bool = True,
    ) -> None:
        provenance = self._prov("dump", statement, "constraint")
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
                    provenance=provenance,
                )
            )
        elif isinstance(node, exp.CheckColumnConstraint):
            predicate = node.this.sql(dialect="postgres")
            table.checks.append(
                CheckConstraint(
                    name=name or f"{table.name}_check_{len(table.checks) + 1}",
                    definition=f"CHECK ({predicate})",
                    predicate=predicate,
                    validated=validated,
                    provenance=provenance,
                )
            )
        elif _EXCLUDE is not None and isinstance(node, _EXCLUDE):
            self._add_exclusion(table, name, node, statement, provenance)
        elif isinstance(node, exp.ForeignKey):
            columns = [item.name for item in node.expressions]
            reference = node.args.get("reference")
            if reference is not None:
                self._add_foreign_key(
                    table,
                    name or f"{table.name}_{columns[0]}_fkey",
                    columns,
                    reference,
                    provenance,
                )

    def _add_exclusion(
        self,
        table: Table,
        name: str | None,
        node: Any,
        statement: str,
        provenance: Provenance,
    ) -> None:
        params = node.this
        if not isinstance(params, exp.IndexParameters):
            return
        elements: list[ExclusionElement] = []
        for item in params.args.get("columns") or []:
            if not isinstance(item, exp.WithOperator):
                continue
            target = item.this
            if isinstance(target, exp.Ordered):
                target = target.this
            if not isinstance(target, exp.Column):
                continue
            operator = item.args.get("op")
            operator_name = operator.name if hasattr(operator, "name") else str(operator)
            if operator_name:
                elements.append(
                    ExclusionElement(column=target.name, operator=operator_name)
                )
        if not elements:
            return
        where = params.args.get("where")
        predicate = None
        if where is not None:
            predicate = (
                where.this.copy()
                .transform(
                    lambda node: (
                        exp.column(node.name, table="a")
                        if isinstance(node, exp.Column) and not node.table
                        else node
                    )
                )
                .sql(dialect="postgres")
            )
        method = params.args.get("using")
        table.exclusions.append(
            ExclusionConstraint(
                name=name or f"{table.name}_{elements[0].column}_excl",
                elements=elements,
                method=method.name if hasattr(method, "name") else None,
                predicate=predicate,
                definition=statement,
                provenance=provenance,
            )
        )

    def _add_foreign_key(
        self,
        table: Table,
        name: str,
        child_columns: list[str],
        reference: Any,
        provenance: Provenance,
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
                provenance=provenance,
            )
        )

    def _alter_table(self, statement: str) -> None:
        if self._alter_attach_partition(statement):
            return

        tree = sqlglot.parse_one(statement, read="postgres")
        if not isinstance(tree, exp.Alter):
            self._store_object("alter_table", statement)
            return
        table_expr = tree.this
        if not isinstance(table_expr, exp.Table):
            return
        schema = table_expr.db or self.search_path[0]
        table = self.tables.get(self._table_key(schema, table_expr.name))
        if table is None:
            return

        not_valid = "NOT VALID" in statement.upper()
        for action in tree.args.get("actions") or []:
            if isinstance(action, exp.AddConstraint):
                for node in action.expressions:
                    if isinstance(node, exp.Constraint):
                        for sub in node.expressions:
                            self._apply_constraint(
                                table, node.name, sub, statement, validated=not not_valid
                            )
                    else:
                        self._apply_constraint(
                            table, None, node, statement, validated=not not_valid
                        )
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

        for match in re.finditer(
            rf"ALTER{_WS}COLUMN{_WS}({_IDENT}){_WS}SET{_WS}NOT{_WS}NULL",
            statement,
            re.IGNORECASE,
        ):
            try:
                table.column(_unquote(match.group(1))).nullable = False
            except KeyError:
                pass
        for match in re.finditer(
            rf"ALTER{_WS}COLUMN{_WS}({_IDENT}){_WS}DROP{_WS}NOT{_WS}NULL",
            statement,
            re.IGNORECASE,
        ):
            try:
                table.column(_unquote(match.group(1))).nullable = True
            except KeyError:
                pass
        if "ENABLE ROW LEVEL SECURITY" in statement.upper() or "FORCE ROW LEVEL SECURITY" in statement.upper():
            self._store_object("policy_rls", statement)

    def _alter_attach_partition(self, statement: str) -> bool:
        match = re.search(
            rf"ALTER{_WS}TABLE{_WS}(?:ONLY{_WS})?({_QUALIFIED}){_WS}ATTACH{_WS}PARTITION{_WS}"
            rf"({_QUALIFIED}){_WS}(FOR{_WS}VALUES{_WS}.*?|DEFAULT)\s*$",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return False
        parent_schema, parent_name = self._qualified(match.group(1))
        child_schema, child_name = self._qualified(match.group(2), parent_schema)
        bound = " ".join(match.group(3).split())
        child_key = self._table_key(child_schema, child_name)
        self.attach_relations.append(
            {
                "parent": self._table_key(parent_schema, parent_name),
                "child_schema": child_schema,
                "child_name": child_name,
                "bound": bound,
            }
        )
        self.tables.pop(child_key, None)
        return True

    def _create_index(self, statement: str) -> None:
        self._store_object("index", statement)
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
                    provenance=self._prov("dump", statement, "unique_index"),
                )
            )

    # ---------------------------------------------------------- like/inherits
    def _resolve_like_and_inherits(self) -> None:
        specs: list[dict[str, Any]] = list(self.like_specs) + list(self.inherits_specs)
        for _ in range(len(specs) + 1):
            pending: list[dict[str, Any]] = []
            for spec in specs:
                child = self.tables.get(spec["child_key"])
                parent = self.tables.get(spec["parent_key"])
                if child is None or parent is None:
                    pending.append(spec)
                    continue
                is_like = spec in self.like_specs
                options = {option.upper() for option in spec.get("options", [])}
                including_all = "ALL" in options
                including_constraints = including_all or "CONSTRAINTS" in options
                including_defaults = including_all or "DEFAULTS" in options
                existing = {column.name for column in child.columns}
                for column in parent.columns:
                    if column.name in existing:
                        continue
                    copied = deepcopy(column)
                    if not including_defaults:
                        copied.default = None
                    child.columns.append(copied)
                if is_like and including_constraints:
                    child.primary_key = list(parent.primary_key)
                    child.uniques.extend(deepcopy(parent.uniques))
                    child.checks.extend(deepcopy(parent.checks))
                    child.foreign_keys.extend(deepcopy(parent.foreign_keys))
                if not is_like:
                    child.checks.extend(
                        CheckConstraint(
                            name=check.name,
                            definition=check.definition,
                            predicate=check.predicate,
                            validated=check.validated,
                            provenance=Provenance(
                                kind="inherited",
                                source=parent.key,
                                detail=check.name,
                            ),
                        )
                        for check in parent.checks
                    )
            if not pending:
                return
            specs = pending

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
        relations = list(self.partition_relations) + list(self.attach_relations)
        if not relations:
            return
        for key, (strategy, columns) in self.partition_specs.items():
            table = self.tables.get(key)
            if table is not None:
                table.is_partitioned = True
                table.partition_strategy = strategy
                table.partition_key = columns

        parent_of: dict[str, str] = {}
        for relation in relations:
            parent_of[self._table_key(relation["child_schema"], relation["child_name"])] = relation[
                "parent"
            ]

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
        for relation in relations:
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
        kind = head.group(1).lower()
        schema, name = self._qualified(head.group(2))
        body_match = re.search(r"\bAS\s+(\$[A-Za-z_0-9]*\$)(.*?)\1", statement, re.DOTALL)
        body = body_match.group(2).strip() if body_match else ""
        language_match = re.search(r"\bLANGUAGE\s+([\w]+)", statement, re.IGNORECASE)
        language = language_match.group(1) if language_match else None
        returns_match = re.search(
            r"\bRETURNS\s+(.+?)\s+(?:LANGUAGE|AS|IMMUTABLE|STABLE|VOLATILE|SECURITY|SET|$)",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        returns = " ".join(returns_match.group(1).split()) if returns_match else None
        function = Function(
            oid=len(self.functions) + 1,
            schema=schema,
            name=name,
            body=body,
            kind=kind,
            language=language,
            returns=returns,
            provenance=self._prov("dump", statement, kind),
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
        when_match = re.search(r"\bWHEN\s*\((.*?)\)\s*EXECUTE", statement, re.IGNORECASE | re.DOTALL)
        when_clause = " ".join(when_match.group(1).split()) if when_match else None
        update_of: list[str] = []
        update_match = re.search(r"UPDATE\s+OF\s+([\w\s,\"]+?)\s+ON", statement, re.IGNORECASE)
        if update_match:
            update_of = [
                _unquote(item.strip())
                for item in update_match.group(1).split(",")
                if item.strip()
            ]
        self.trigger_specs.append(
            {
                "name": name,
                "timing": timing,
                "events": events,
                "table_key": self._table_key(table_schema, table_name),
                "function_name": function_name,
                "function_key": f"{function_schema}.{function_name}",
                "definition": statement,
                "when_clause": when_clause,
                "update_of": update_of,
                "row_level": "FOR EACH STATEMENT" not in statement.upper(),
            }
        )

    def _attach_triggers(self) -> None:
        for spec in self.trigger_specs:
            table = self.tables.get(spec["table_key"])
            if table is None:
                continue
            function = self.function_by_name.get(
                spec["function_key"]
            ) or self.function_by_name.get(spec["function_name"])
            table.triggers.append(
                Trigger(
                    name=spec["name"],
                    timing=spec["timing"],
                    events=spec["events"],
                    function=spec["function_name"],
                    definition=spec["definition"],
                    function_oid=function.oid if function else None,
                    when_clause=spec.get("when_clause"),
                    update_of=spec.get("update_of", []),
                    row_level=spec.get("row_level", True),
                    provenance=self._prov("dump", spec["definition"], "trigger"),
                )
            )

    def _analyze_functions(self) -> None:
        for function in self.functions:
            body = function.body or ""
            function.statements = self._extract_sql_strings(body)
            function.conditions = self._extract_conditions(body)
            function.writes = sorted(
                {
                    _unquote(match.group(2))
                    for match in re.finditer(
                        rf"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+({_QUALIFIED})",
                        body,
                        re.IGNORECASE,
                    )
                }
            )
            function.reads = sorted(
                {
                    _unquote(match.group(1))
                    for match in re.finditer(rf"\bFROM\s+({_QUALIFIED})", body, re.IGNORECASE)
                }
            )
            for statement in function.statements:
                try:
                    tree = sqlglot.parse_one(statement, read="postgres")
                except Exception as exc:  # noqa: BLE001 - dynamic SQL is best effort
                    logger.debug("skipping dynamic SQL: %s", exc)
                    continue
                self._infer_joins_from_query(tree, Provenance("function", f"{function.schema}.{function.name}"))

    def _extract_sql_strings(self, body: str) -> list[str]:
        statements: list[str] = []
        for match in re.finditer(
            r"EXECUTE\s+(?:FORMAT\s*\(\s*)?'((?:[^']|'')*)'", body, re.IGNORECASE | re.DOTALL
        ):
            statements.append(match.group(1).replace("''", "'"))
        for match in re.finditer(
            r"EXECUTE\s+(?:FORMAT\s*\(\s*)?(\$[A-Za-z_0-9]*\$)(.*?)\1", body, re.IGNORECASE | re.DOTALL
        ):
            statements.append(match.group(2).strip())
        return statements

    def _extract_conditions(self, body: str) -> list[str]:
        conditions: list[str] = []
        for match in re.finditer(r"\bIF\s+(.+?)\s+THEN\b", body, re.IGNORECASE | re.DOTALL):
            conditions.append(" ".join(match.group(1).split()))
        for match in re.finditer(r"\bWHEN\s+(.+?)\s+THEN\b", body, re.IGNORECASE | re.DOTALL):
            conditions.append(" ".join(match.group(1).split()))
        return conditions

    # --------------------------------------------------------- extracted rules
    def _extract_rules(self) -> None:
        trigger_by_function: dict[tuple[str, str], dict[str, Any]] = {}
        for spec in self.trigger_specs:
            function = self.function_by_name.get(
                spec["function_key"]
            ) or self.function_by_name.get(spec["function_name"])
            if function is not None:
                trigger_by_function[(function.schema, function.name)] = spec

        for function in self.functions:
            spec = trigger_by_function.get((function.schema, function.name))
            if spec is not None:
                table_key = spec["table_key"]
                provenance = Provenance(
                    kind="trigger",
                    source=spec["name"],
                    detail=f"{function.schema}.{function.name}",
                )
            else:
                table_key = None
                provenance = Provenance(
                    kind="function",
                    source=f"{function.schema}.{function.name}",
                )
            self._rules_from_body(function.body, table_key, provenance)

    def _rules_from_body(
        self,
        body: str,
        table_key: str | None,
        provenance: Provenance,
    ) -> None:
        if not body:
            return
        table = self.tables.get(table_key) if table_key else None
        known_columns = {column.name for column in table.columns} if table else set()

        blocks = _if_blocks(body)
        for index, (condition, block, start, end) in enumerate(blocks):
            if any(
                other_start < start and end <= other_end
                for other_index, (_, _, other_start, other_end) in enumerate(blocks)
                if other_index != index
            ):
                continue
            raise_match = re.search(
                r"\bRAISE\s+(?:EXCEPTION|ERROR)\b", block, re.IGNORECASE
            )
            if raise_match is None:
                continue
            nested = re.search(r"\bIF\b", block, re.IGNORECASE)
            if nested is not None and nested.start() < raise_match.start():
                continue
            else_match = re.search(r"\bELSE\b", block, re.IGNORECASE)
            if else_match is not None and else_match.start() < raise_match.start():
                continue
            negated = _negate_condition(condition)
            if negated is None:
                continue
            expression, confidence, columns = negated
            enforce = (
                table is not None
                and confidence >= 0.9
                and _identifiers(expression) <= known_columns
                and _generation_supported(expression)
            )
            self._add_rule(
                RuleCandidate(
                    id=f"rule:{table_key or 'function'}:{len(self.rules) + 1}",
                    kind="check",
                    table=table_key or "",
                    expression=expression,
                    columns=columns,
                    confidence=confidence,
                    applied=enforce,
                    provenance=provenance,
                    note=f"IF {condition} THEN RAISE",
                )
            )

        for target, raw_expression in re.findall(
            r"NEW\.(\w+)\s*:=\s*(.+?);", body, re.IGNORECASE | re.DOTALL
        ):
            expression = _strip_row_vars(" ".join(raw_expression.split()))
            identifiers = _identifiers(expression)
            confidence = _derived_confidence(expression, target, known_columns)
            self_referential = target in identifiers
            enforce = (
                table is not None
                and confidence >= 0.9
                and target in known_columns
                and not self_referential
                and identifiers <= known_columns
                and _generation_supported(f"{target} = {expression}")
            )
            note = f"NEW.{target} := {expression}"
            if self_referential:
                note += " (self-referential; not a static invariant)"
            self._add_rule(
                RuleCandidate(
                    id=f"rule:{table_key or 'function'}:{len(self.rules) + 1}",
                    kind="derived",
                    table=table_key or "",
                    expression=f"{target} = {expression}",
                    columns=sorted(identifiers | {target}),
                    confidence=confidence,
                    applied=enforce,
                    provenance=provenance,
                    note=note,
                )
            )

    def _add_rule(self, rule: RuleCandidate) -> None:
        key = (rule.table, rule.kind, rule.expression)
        if key in self._rule_keys:
            return
        self._rule_keys.add(key)
        if rule.applied and rule.scope == "row" and rule.table in self.tables:
            table = self.tables[rule.table]
            table.checks.append(
                CheckConstraint(
                    name=rule.id,
                    definition=f"CHECK ({rule.expression})",
                    predicate=rule.expression,
                    provenance=rule.provenance,
                )
            )
        self.rules.append(rule)

    # --------------------------------------------------------- relationships
    def _infer_relationships(self) -> None:
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
                        provenance=Provenance("inferred", "naming", f"{table.key}.{column.name}"),
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

    def _infer_joins_from_query(self, query: Any, provenance: Provenance) -> None:
        aliases = self._query_aliases(query)
        for join in query.find_all(exp.Join):
            on = join.args.get("on")
            if not isinstance(on, exp.EQ):
                continue
            left = on.this if isinstance(on.this, exp.Column) else None
            right = on.expression if isinstance(on.expression, exp.Column) else None
            if left is None or right is None:
                continue
            left_ref = self._resolve_column(left, aliases)
            right_ref = self._resolve_column(right, aliases)
            if left_ref is None or right_ref is None:
                continue
            left_table, left_column = left_ref
            right_table, right_column = right_ref
            if left_column in left_table.primary_key and right_column not in right_table.primary_key:
                self._add_inferred_fk(right_table, right_column, left_table, left_column, provenance)
            elif right_column in right_table.primary_key and left_column not in left_table.primary_key:
                self._add_inferred_fk(left_table, left_column, right_table, right_column, provenance)

    def _query_aliases(self, query: Any) -> dict[str, tuple[str, str]]:
        aliases: dict[str, tuple[str, str]] = {}
        for table in query.find_all(exp.Table):
            schema = table.db or self.search_path[0]
            aliases[table.alias_or_name] = (schema, table.name)
            aliases[f"{schema}.{table.name}"] = (schema, table.name)
        return aliases

    def _filters_from_query(
        self, query: Any, statement: str, provenance: Provenance
    ) -> None:
        aliases = self._query_aliases(query)
        for where in query.find_all(exp.Where):
            predicate_node = where.this
            if predicate_node is None:
                continue
            predicate = predicate_node.sql(dialect="postgres")
            table_key = self._filter_table(predicate_node, aliases)
            self._add_rule(
                RuleCandidate(
                    id=f"rule:filter:{len(self.rules) + 1}",
                    kind="filter",
                    table=table_key,
                    expression=predicate,
                    columns=sorted(_identifiers(predicate)),
                    confidence=0.7,
                    applied=False,
                    provenance=provenance,
                    note=f"view filter in {provenance.source}",
                    scope="aggregate",
                )
            )

    def _filter_table(
        self, predicate: Any, aliases: dict[str, tuple[str, str]]
    ) -> str:
        tables: list[str] = []
        for column in predicate.find_all(exp.Column):
            if column.table:
                resolved = self._resolve_column(column, aliases)
                if resolved is not None:
                    tables.append(resolved[0].key)
                continue
            matches = []
            for schema, name in aliases.values():
                table = self.tables.get(self._table_key(schema, name))
                if table is not None and any(
                    item.name == column.name for item in table.columns
                ):
                    matches.append(table.key)
            if len(set(matches)) == 1:
                tables.append(matches[0])
        unique = sorted(set(tables))
        return unique[0] if len(unique) == 1 else ""

    def _resolve_column(
        self, column: exp.Column, aliases: dict[str, tuple[str, str]]
    ) -> tuple[Table, str] | None:
        table_ref = column.table
        if not table_ref:
            return None
        key = aliases.get(table_ref) or aliases.get(_unquote(table_ref))
        if key is None:
            return None
        table = self.tables.get(self._table_key(*key))
        if table is None:
            return None
        return table, column.name

    def _add_inferred_fk(
        self,
        child: Table,
        child_column: str,
        parent: Table,
        parent_column: str,
        provenance: Provenance,
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
                provenance=provenance,
            )
        )

    def _create_view(self, statement: str, materialized: bool = False) -> None:
        kind = "materialized_view" if materialized else "view"
        self._store_object(kind, statement)
        try:
            tree = sqlglot.parse_one(statement, read="postgres")
        except Exception:  # noqa: BLE001 - a view we cannot parse is still stored raw
            return
        if isinstance(tree, exp.Create) and tree.expression is not None:
            provenance = Provenance(kind, statement.splitlines()[0][:200])
            self._infer_joins_from_query(tree.expression, provenance)
            self._filters_from_query(tree.expression, statement, provenance)


def _clean_sql(text: str) -> str:
    return " ".join(text.split())


def _strip_row_vars(text: str) -> str:
    return re.sub(r"\b(?:NEW|OLD)\.", "", text, flags=re.IGNORECASE)


def _strip_outer_parens(text: str) -> str:
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        balanced = True
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        text = text[1:-1].strip()
    return text


def _if_blocks(body: str) -> list[tuple[str, str, int, int]]:
    """Return ``(condition, block, start, end)`` for every ``IF ... END IF``."""
    blocks: list[tuple[str, str, int, int]] = []
    for match in re.finditer(r"\bIF\b", body, re.IGNORECASE):
        prefix = body[max(0, match.start() - 32) : match.start()]
        if re.search(r"(?:END|ELSE|ELSIF)\s*$", prefix, re.IGNORECASE):
            continue
        then = re.search(r"\bTHEN\b", body[match.end() :], re.IGNORECASE)
        if then is None:
            continue
        condition = _clean_sql(body[match.end() : match.end() + then.start()])
        if not condition:
            continue
        depth = 1
        end: int | None = None
        start = match.end() + then.end()
        for token in re.finditer(r"\bIF\b", body[start:], re.IGNORECASE):
            before = body[start + max(0, token.start() - 16) : start + token.start()]
            if re.search(r"END\s*$", before, re.IGNORECASE):
                depth -= 1
                if depth == 0:
                    end = start + token.start()
                    break
            else:
                depth += 1
        if end is None:
            continue
        blocks.append((condition, body[start:end], start, end))
    return blocks


_FLIP = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "=": "<>", "<>": "=", "!=": "="}


def _negate_condition(condition: str) -> tuple[str, float, list[str]] | None:
    """Turn ``IF <condition> THEN RAISE`` into the invariant ``NOT <condition>``."""
    text = _strip_outer_parens(_strip_row_vars(_clean_sql(condition)))
    if not text:
        return None

    if re.match(r"^NOT\s+", text, re.IGNORECASE):
        expression = re.sub(r"^NOT\s+", "", text, count=1, flags=re.IGNORECASE)
        return expression, 0.9, sorted(_identifiers(expression))

    match = re.match(r"^(.+?)\s+IS\s+(NOT\s+)?NULL$", text, re.IGNORECASE | re.DOTALL)
    if match:
        expression = f"{match.group(1)} IS {'NULL' if match.group(2) else 'NOT NULL'}"
        return expression, 0.95, sorted(_identifiers(expression))

    match = re.match(r"^(.+?)\s+IS\s+(NOT\s+)?DISTINCT\s+FROM\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
    if match:
        qualifier = "DISTINCT FROM" if match.group(2) else "NOT DISTINCT FROM"
        expression = f"{match.group(1)} IS {qualifier} {match.group(3)}"
        return expression, 0.9, sorted(_identifiers(expression))

    match = re.match(r"^(.+?)\s+BETWEEN\s+(.+?)\s+AND\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
    if match:
        expression = f"{match.group(1)} NOT BETWEEN {match.group(2)} AND {match.group(3)}"
        return expression, 0.9, sorted(_identifiers(expression))

    if re.search(r"\b(AND|OR)\b", text, re.IGNORECASE):
        expression = f"NOT ({text})"
        return expression, 0.6, sorted(_identifiers(expression))

    match = re.match(r"^(.+?)\s+(NOT\s+)?(IN|LIKE|ILIKE)\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if match:
        operator = match.group(3).upper()
        negative = "" if match.group(2) else "NOT "
        expression = f"{match.group(1)} {negative}{operator} {match.group(4)}"
        return expression, 0.9, sorted(_identifiers(expression))

    match = re.match(r"^(.+?)\s*(<=|>=|<>|!=|=|<|>)\s*(.+)$", text, re.DOTALL)
    if match and match.group(2) in _FLIP:
        expression = (
            f"{match.group(1).strip()} {_FLIP[match.group(2)]} {match.group(3).strip()}"
        )
        return expression, 0.95, sorted(_identifiers(expression))

    return f"NOT ({text})", 0.5, sorted(_identifiers(text))


_NON_COLUMN_WORDS = {
    "and", "or", "not", "is", "null", "true", "false", "in", "between", "like",
    "ilike", "distinct", "from", "as", "case", "when", "then", "else", "end",
    "current_date", "current_time", "current_timestamp", "localtime",
    "localtimestamp", "interval", "year", "month", "day", "hour", "minute",
    "second", "days", "hours", "minutes", "seconds", "weeks", "months", "years",
    "double", "precision", "numeric", "decimal", "integer", "int", "bigint",
    "smallint", "text", "varchar", "character", "varying", "char", "timestamp",
    "timestamptz", "date", "time", "boolean", "bool", "jsonb", "json", "uuid",
    "bytea", "real", "float", "serial", "bigserial", "collate", "epoch",
}

_KNOWN_FUNCTIONS = {
    "abs", "ceil", "ceiling", "floor", "round", "trunc", "length", "char_length",
    "lower", "upper", "trim", "btrim", "ltrim", "rtrim", "coalesce", "nullif",
    "greatest", "least", "concat", "substr", "substring", "replace", "to_char",
    "to_date", "to_timestamp", "date_trunc", "extract", "regexp_replace",
    "split_part", "md5", "now", "random", "nextval", "currval", "setval",
    "gen_random_uuid", "uuid_generate_v4", "make_interval", "age",
}


def _identifiers(expression: str) -> set[str]:
    """Column-like identifiers in an expression, ignoring functions and keywords."""
    cleaned = re.sub(r"'(?:[^']|'')*'", "''", expression)
    cleaned = re.sub(r"\b(?:NEW|OLD)\.", "", cleaned, flags=re.IGNORECASE)
    found: set[str] = set()
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", cleaned):
        name = match.group(1)
        if name.lower() in _NON_COLUMN_WORDS:
            continue
        if cleaned[match.end() :].lstrip().startswith("("):
            continue
        found.add(name)
    return found


def _generation_supported(predicate: str) -> bool:
    text = predicate.strip()
    if re.search(r"\bSELECT\b", text, re.IGNORECASE):
        return False
    if re.search(
        r"\b(?:now|current_timestamp|current_date|current_time|random|nextval|"
        r"clock_timestamp|statement_timestamp|transaction_timestamp)\b",
        text,
        re.IGNORECASE,
    ):
        return False
    literal = r"(?:\d+(?:\.\d+)?|'(?:[^']|'')*'|NULL|TRUE|FALSE)"
    column = r"[A-Za-z_][A-Za-z0-9_]*"
    patterns = [
        rf"^{column}\s*(?:<=|>=|<>|!=|=|<|>)\s*{literal}$",
        rf"^{column}\s+(?:NOT\s+)?IN\s*\(.*\)$",
        rf"^{column}\s+IS\s+(?:NOT\s+)?NULL$",
        rf"^(?:char_length|length)\s*\(\s*{column}\s*\)\s*(?:<=|<|=)\s*\d+$",
        rf"^{column}\s*(?:<=|>=|<>|!=|=|<|>)\s*{column}$",
        rf"^{column}\s*=\s*\(*\s*{column}\s*[+\-*/]\s*{column}\s*\)*$",
        rf"^{column}\s*=\s*{column}$",
    ]
    return any(re.match(pattern, text, re.IGNORECASE) for pattern in patterns)


def _derived_confidence(
    expression: str, target: str, known_columns: set[str]
) -> float:
    lowered = expression.lower()
    identifiers = _identifiers(expression)
    if re.search(r"\bselect\b", lowered):
        return 0.2
    if target in identifiers:
        return 0.3
    if re.search(
        r"\b(?:now|current_timestamp|current_date|current_time|random|nextval|"
        r"clock_timestamp|statement_timestamp|transaction_timestamp|"
        r"gen_random_uuid|uuid_generate_v4)\b",
        lowered,
    ):
        return 0.4
    called = {
        name.lower() for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", expression)
    }
    if called - _KNOWN_FUNCTIONS:
        return 0.6
    if known_columns and not identifiers <= known_columns:
        return 0.6
    if identifiers:
        return 0.9
    return 0.85


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
