"""Load a T-SQL schema dump into the canonical Catalog.

The loader handles the DDL that appears in SQL Server dumps: ``GO`` batches,
bracketed identifiers, ``IDENTITY``, computed columns, ``ROWVERSION``, checks,
foreign keys, unique indexes, views, stored procedures, functions, triggers,
sequences and user-defined types.
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
    Provenance,
    RuleCandidate,
    SqlObject,
    Table,
    Trigger,
    UniqueConstraint,
)
from .dialect import SqlServerDialect

logger = logging.getLogger(__name__)

_IDENT = r"(?:\[[^\]]+\]|[\w$]+)"
_QUALIFIED = rf"{_IDENT}(?:\.{_IDENT})?"
_WS = r"\s+"
_GO = re.compile(r"(?im)^\s*GO\s*$")


def split_batches(sql: str) -> list[str]:
    """Split a T-SQL script on ``GO`` batch separators."""
    return [batch.strip() for batch in _GO.split(sql) if batch.strip()]


def _unquote(name: str) -> str:
    name = name.strip()
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1].replace("]]", "]")
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('""', '"')
    return name


def _qualified(name: str, default_schema: str = "dbo") -> tuple[str, str]:
    parts = re.findall(_IDENT, name)
    if len(parts) >= 2:
        return _unquote(parts[-2]), _unquote(parts[-1])
    return default_schema, _unquote(parts[-1] if parts else name)


def _split_top_level(text: str, separator: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    in_string = False
    in_bracket = False
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            if in_string and index + 1 < len(text) and text[index + 1] == "'":
                current.append("''")
                index += 2
                continue
            in_string = not in_string
            current.append(char)
            index += 1
            continue
        if char == "[" and not in_string:
            in_bracket = True
        elif char == "]" and not in_string:
            in_bracket = False
        elif not in_string and not in_bracket:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == separator and depth == 0:
                parts.append("".join(current).strip())
                current = []
                index += 1
                continue
        current.append(char)
        index += 1
    if current:
        parts.append("".join(current).strip())
    return parts


def _compatible(left: Column, right: Column) -> bool:
    if left.data_kind == right.data_kind:
        return True
    integers = {DataKind.SMALLINT, DataKind.INT, DataKind.BIGINT}
    if left.data_kind in integers and right.data_kind in integers:
        return True
    text = {DataKind.TEXT, DataKind.ENUM}
    return bool(left.data_kind in text and right.data_kind in text)


class SqlServerDumpLoader:
    def __init__(self) -> None:
        self.dialect = SqlServerDialect()
        self.tables: dict[str, Table] = {}
        self.functions: list[Function] = []
        self.objects: list[SqlObject] = []
        self.trigger_specs: list[dict[str, Any]] = []
        self.rules: list[RuleCandidate] = []
        self.schemas: set[str] = {"dbo"}
        self.search_path = "dbo"

    # ------------------------------------------------------------------ entry
    def load(self, sql: str) -> Catalog:
        for batch in split_batches(sql):
            try:
                self._batch(batch)
            except Exception as exc:  # noqa: BLE001 - one bad batch must not drop the dump
                logger.warning("skipping unparseable T-SQL batch: %s", exc)
        self._attach_triggers()
        self._infer_relationships()
        self._analyze_routines()
        self._extract_rules()
        return Catalog(
            database="dump",
            server_version="tsql",
            tables=sorted(self.tables.values(), key=lambda table: table.key),
            schemas=sorted({table.schema for table in self.tables.values()} | self.schemas),
            functions=self.functions,
            objects=self.objects,
            rules=self.rules,
        )

    def _batch(self, batch: str) -> None:
        text = batch.strip()
        upper = text.upper()
        if upper.startswith("USE "):
            match = re.search(rf"USE\s+({_IDENT})", text, re.IGNORECASE)
            if match:
                self.search_path = _unquote(match.group(1))
            return
        if upper.startswith(("SET ", "PRINT ", ":R ", "DECLARE ")):
            return
        # A batch can start with SET/USE statements before the DDL. Find the
        # first CREATE/ALTER token that is not inside a comment.
        start = self._ddl_start(text)
        if start is None:
            return
        statement = text[start:]

        if re.match(r"(?is)^CREATE\s+SCHEMA\b", statement):
            self._create_schema(statement)
        elif re.match(r"(?is)^CREATE\s+TABLE\b", statement):
            self._create_table(statement)
        elif re.match(r"(?is)^ALTER\s+TABLE\b", statement):
            self._alter_table(statement)
        elif re.match(r"(?is)^CREATE\s+(?:UNIQUE\s+)?(?:NONCLUSTERED\s+|CLUSTERED\s+)?INDEX\b", statement):
            self._create_index(statement)
        elif re.match(r"(?is)^CREATE\s+(?:OR\s+ALTER\s+)?VIEW\b", statement):
            self._create_view(statement)
        elif re.match(r"(?is)^CREATE\s+(?:OR\s+ALTER\s+)?(?:PROC|PROCEDURE)\b", statement):
            self._create_routine(statement, "procedure")
        elif re.match(r"(?is)^CREATE\s+(?:OR\s+ALTER\s+)?FUNCTION\b", statement):
            self._create_routine(statement, "function")
        elif re.match(r"(?is)^CREATE\s+(?:OR\s+ALTER\s+)?TRIGGER\b", statement):
            self._create_trigger(statement)
        elif re.match(r"(?is)^CREATE\s+SEQUENCE\b", statement):
            self._store_object("sequence", statement)
        elif re.match(r"(?is)^CREATE\s+TYPE\b", statement):
            self._store_object("type", statement)
        elif re.match(r"(?is)^CREATE\s+SYNONYM\b", statement):
            self._store_object("synonym", statement)
        elif re.match(r"(?is)^CREATE\s+", statement):
            self._store_object("object", statement)

    def _ddl_start(self, text: str) -> int | None:
        index = 0
        in_line = in_block = in_string = False
        while index < len(text):
            char = text[index]
            nxt = text[index + 1] if index + 1 < len(text) else ""
            if in_line:
                if char == "\n":
                    in_line = False
                index += 1
                continue
            if in_block:
                if char == "*" and nxt == "/":
                    in_block = False
                    index += 2
                    continue
                index += 1
                continue
            if in_string:
                if char == "'" and nxt == "'":
                    index += 2
                    continue
                if char == "'":
                    in_string = False
                index += 1
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
                in_string = True
                index += 1
                continue
            if text[index : index + 6].upper() == "CREATE" or text[index : index + 5].upper() == "ALTER":
                before = text[max(0, index - 1) : index]
                if before in {"", ";", "\n", " ", "\t", "("}:
                    return index
            index += 1
        return None

    def _prov(self, source: str, detail: str) -> Provenance:
        first = source.splitlines()[0].strip() if source else ""
        return Provenance("dump", first[:200], detail)

    # ---------------------------------------------------------------- schemas
    def _create_schema(self, statement: str) -> None:
        match = re.search(rf"CREATE\s+SCHEMA\s+({_IDENT})", statement, re.IGNORECASE)
        if match:
            self.schemas.add(_unquote(match.group(1)))

    def _store_object(self, kind: str, statement: str) -> None:
        schema, name = None, None
        patterns = {
            "view": rf"CREATE\s+(?:OR\s+ALTER\s+)?VIEW\s+({_QUALIFIED})",
            "sequence": rf"CREATE\s+SEQUENCE\s+({_QUALIFIED})",
            "type": rf"CREATE\s+TYPE\s+({_QUALIFIED})",
            "synonym": rf"CREATE\s+SYNONYM\s+({_QUALIFIED})",
        }
        pattern = patterns.get(kind)
        if pattern:
            match = re.search(pattern, statement, re.IGNORECASE)
            if match:
                schema, name = _qualified(match.group(1), self.search_path)
        self.objects.append(
            SqlObject(
                kind=kind,
                schema=schema,
                name=name,
                definition=statement,
                provenance=self._prov(statement, kind),
            )
        )

    # ----------------------------------------------------------------- tables
    def _create_table(self, statement: str) -> None:
        tree = sqlglot.parse_one(statement, read="tsql")
        if not isinstance(tree, exp.Create) or tree.kind != "TABLE":
            self._store_object("table", statement)
            return
        schema_expr = tree.this
        if not isinstance(schema_expr, exp.Schema):
            self._store_object("table", statement)
            return
        table_expr = schema_expr.this
        if not isinstance(table_expr, exp.Table):
            return
        schema = _unquote(table_expr.db) or self.search_path
        name = _unquote(table_expr.name)
        table = Table(
            schema=schema,
            name=name,
            provenance=self._prov(statement, "table"),
        )
        for node in schema_expr.expressions:
            if isinstance(node, exp.ColumnDef):
                self._add_column(table, node, statement)
            elif isinstance(node, exp.Constraint):
                for sub in node.expressions:
                    self._apply_constraint(table, node.name, sub, statement)
            elif isinstance(
                node,
                (
                    exp.PrimaryKey,
                    exp.UniqueColumnConstraint,
                    exp.CheckColumnConstraint,
                    exp.ForeignKey,
                ),
            ):
                self._apply_constraint(table, None, node, statement)
        self.tables[table.key] = table

    def _add_column(self, table: Table, node: exp.ColumnDef, statement: str) -> None:
        kind = node.args.get("kind")
        sql_type = kind.sql(dialect="tsql") if kind is not None else "computed"
        column = Column(
            name=_unquote(node.name),
            sql_type=sql_type,
            nullable=True,
            data_kind=self.dialect.classify_type(sql_type),
        )
        for constraint in node.args.get("constraints") or []:
            kind_node = constraint.args.get("kind")
            if isinstance(kind_node, exp.NotNullColumnConstraint):
                column.nullable = False
            elif isinstance(kind_node, exp.PrimaryKeyColumnConstraint):
                column.nullable = False
                if column.name not in table.primary_key:
                    table.primary_key.append(column.name)
            elif isinstance(kind_node, exp.GeneratedAsIdentityColumnConstraint):
                column.identity = "a"
            elif isinstance(kind_node, exp.DefaultColumnConstraint):
                column.default = kind_node.this.sql(dialect="tsql")
            elif isinstance(kind_node, exp.ComputedColumnConstraint):
                column.generated = "computed"
                column.default = kind_node.this.sql(dialect="tsql")
            elif isinstance(kind_node, exp.UniqueColumnConstraint):
                table.uniques.append(
                    UniqueConstraint(
                        name=constraint.name or f"{table.name}_{column.name}_key",
                        columns=[column.name],
                        provenance=self._prov(statement, "unique"),
                    )
                )
            elif isinstance(kind_node, exp.Reference):
                self._add_foreign_key(
                    table,
                    constraint.name or f"{table.name}_{column.name}_fkey",
                    [column.name],
                    kind_node,
                    statement,
                )
            elif isinstance(kind_node, exp.CheckColumnConstraint):
                predicate = kind_node.this.sql(dialect="tsql")
                table.checks.append(
                    CheckConstraint(
                        name=constraint.name or f"{table.name}_{column.name}_check",
                        definition=f"CHECK ({predicate})",
                        predicate=predicate,
                        provenance=self._prov(statement, "check"),
                    )
                )
        if any(
            isinstance(constraint.args.get("kind"), exp.ComputedColumnConstraint)
            for constraint in node.args.get("constraints") or []
        ):
            table.columns.append(column)
            return
        if any(
            isinstance(constraint.args.get("kind"), exp.GeneratedAsIdentityColumnConstraint)
            for constraint in node.args.get("constraints") or []
        ):
            column.identity = "a"
        if kind is not None and getattr(kind, "this", None) == exp.DataType.Type.ROWVERSION:
            column.generated = "rowversion"
        if kind is not None and getattr(kind, "this", None) == exp.DataType.Type.TIMESTAMP:
            # SQL Server TIMESTAMP is an alias for ROWVERSION; sqlglot maps both
            # to different AST nodes depending on the dialect input.
            pass
        table.columns.append(column)

    def _apply_constraint(
        self,
        table: Table,
        name: str | None,
        node: Any,
        statement: str,
    ) -> None:
        provenance = self._prov(statement, "constraint")
        if isinstance(node, exp.PrimaryKey):
            table.primary_key = [item.name for item in node.expressions]
            for column_name in table.primary_key:
                try:
                    table.column(column_name).nullable = False
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
            predicate = node.this.sql(dialect="tsql")
            table.checks.append(
                CheckConstraint(
                    name=name or f"{table.name}_check_{len(table.checks) + 1}",
                    definition=f"CHECK ({predicate})",
                    predicate=predicate,
                    provenance=provenance,
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
                    statement,
                )

    def _add_foreign_key(
        self,
        table: Table,
        name: str,
        child_columns: list[str],
        reference: Any,
        statement: str,
    ) -> None:
        ref_schema = reference.this
        if not isinstance(ref_schema, exp.Schema):
            return
        parent_expr = ref_schema.this
        if not isinstance(parent_expr, exp.Table):
            return
        parent_schema = _unquote(parent_expr.db) or table.schema
        parent_columns = [item.name for item in ref_schema.expressions]
        table.foreign_keys.append(
            ForeignKey(
                name=name,
                child_schema=table.schema,
                child_table=table.name,
                child_columns=child_columns,
                parent_schema=parent_schema,
                parent_table=_unquote(parent_expr.name),
                parent_columns=parent_columns,
                provenance=self._prov(statement, "foreign_key"),
            )
        )

    def _alter_table(self, statement: str) -> None:
        tree = sqlglot.parse_one(statement, read="tsql")
        if not isinstance(tree, exp.Alter):
            return
        table_expr = tree.this
        if not isinstance(table_expr, exp.Table):
            return
        schema = _unquote(table_expr.db) or self.search_path
        table = self.tables.get(f"{schema}.{_unquote(table_expr.name)}")
        if table is None:
            return
        for action in tree.args.get("actions") or []:
            if isinstance(action, exp.AddConstraint):
                for node in action.expressions:
                    if isinstance(node, exp.Constraint):
                        for sub in node.expressions:
                            self._apply_constraint(table, node.name, sub, statement)
                    else:
                        self._apply_constraint(table, None, node, statement)
            elif isinstance(action, exp.AddColumn):
                for node in action.expressions:
                    if isinstance(node, exp.ColumnDef):
                        self._add_column(table, node, statement)
        # ALTER TABLE ... ADD CONSTRAINT ... DEFAULT x FOR [col]
        for match in re.finditer(
            rf"ADD\s+CONSTRAINT\s+({_IDENT})\s+DEFAULT\s+(.+?)\s+FOR\s+({_IDENT})",
            statement,
            re.IGNORECASE | re.DOTALL,
        ):
            column_name = _unquote(match.group(3))
            try:
                table.column(column_name).default = match.group(2).strip()
            except KeyError:
                pass

    # ---------------------------------------------------------------- indexes
    def _create_index(self, statement: str) -> None:
        tree = sqlglot.parse_one(statement, read="tsql")
        if not isinstance(tree, exp.Create) or tree.kind not in {"INDEX", "NONCLUSTERED INDEX"}:
            self._store_object("index", statement)
            return
        index = tree.this
        if not isinstance(index, exp.Index):
            return
        table_expr = index.args.get("table")
        if not isinstance(table_expr, exp.Table):
            return
        schema = _unquote(table_expr.db) or self.search_path
        table = self.tables.get(f"{schema}.{_unquote(table_expr.name)}")
        if table is None:
            return
        params = index.args.get("params")
        where = params.args.get("where") if params is not None else None
        columns: list[str] = []
        for item in getattr(params, "args", {}).get("columns", []) if params else []:
            target = item.this if isinstance(item, exp.Ordered) else item
            if isinstance(target, exp.Column):
                columns.append(_unquote(target.name))
            else:
                columns = []
                break
        if not tree.args.get("unique") or not columns:
            self._store_object("index", statement)
            return
        predicate = (
            where.this.sql(dialect="tsql") if where is not None else None
        )
        table.uniques.append(
            UniqueConstraint(
                name=index.name or f"{table.name}_{columns[0]}_key",
                columns=columns,
                predicate=predicate,
                provenance=self._prov(statement, "unique_index"),
            )
        )

    # ------------------------------------------------------- views/routines
    def _create_view(self, statement: str) -> None:
        self._store_object("view", statement)
        try:
            tree = sqlglot.parse_one(statement, read="tsql")
        except Exception as exc:  # noqa: BLE001 - raw view is still stored
            logger.debug("cannot parse view dependencies: %s", exc)
            return
        if not isinstance(tree, exp.Create) or tree.expression is None:
            return
        schema_expr = tree.this
        name = schema_expr.name if isinstance(schema_expr, exp.Table) else None
        schema = (
            _unquote(schema_expr.db)
            if isinstance(schema_expr, exp.Table) and schema_expr.db
            else self.search_path
        )
        depends_on = []
        own = f"{schema}.{name}" if name else None
        for table in tree.expression.find_all(exp.Table):
            dep_schema = _unquote(table.db) or self.search_path
            dep = f"{dep_schema}.{_unquote(table.name)}"
            if dep != own and dep not in depends_on:
                depends_on.append(dep)
        for obj in reversed(self.objects):
            if obj.kind == "view" and obj.schema == schema and obj.name == _unquote(name or ""):
                obj.depends_on = depends_on
                break

    def _create_routine(self, statement: str, kind: str) -> None:
        pattern = (
            rf"(?is)^CREATE\s+(?:OR\s+ALTER\s+)?(PROC(?:EDURE)?|FUNCTION)\s+"
            rf"({_QUALIFIED})\s*(.*?)\bAS\b\s*"
            rf"(?=(?:BEGIN|SET|SELECT|INSERT|UPDATE|DELETE|DECLARE|WITH|MERGE|"
            rf"IF|RETURN|EXEC|RAISERROR|THROW)\b)(.*)$"
        )
        match = re.match(pattern, statement)
        if not match:
            return
        schema, name = _qualified(match.group(2), self.search_path)
        header = match.group(3)
        returns = None
        if kind == "function":
            returns_match = re.search(r"\bRETURNS\s+(.+?)\s*$", header, re.IGNORECASE | re.DOTALL)
            if returns_match:
                returns = " ".join(returns_match.group(1).split())
                header = header[: returns_match.start()]
        parameters = [
            part.strip()
            for part in _split_top_level(header)
            if part.strip()
        ]
        body = match.group(4).strip()
        function = Function(
            oid=len(self.functions) + 1,
            schema=schema,
            name=name,
            body=body,
            kind=kind,
            language="tsql",
            returns=returns,
            parameters=parameters,
            provenance=self._prov(statement, kind),
        )
        self.functions.append(function)

    def _create_trigger(self, statement: str) -> None:
        match = re.match(
            rf"(?is)^CREATE\s+(?:OR\s+ALTER\s+)?TRIGGER\s+({_QUALIFIED})\s+"
            rf"ON\s+({_QUALIFIED})\s+(AFTER|FOR|INSTEAD\s+OF)\s+"
            rf"([A-Za-z,\s]+?)\s+AS\s+(.*)$",
            statement,
        )
        if not match:
            return
        _schema, name = _qualified(match.group(1), self.search_path)
        table_schema, table_name = _qualified(match.group(2), self.search_path)
        timing = "INSTEAD OF" if match.group(3).upper().startswith("INSTEAD") else "AFTER"
        events = ", ".join(
            part.strip().upper()
            for part in match.group(4).split(",")
            if part.strip()
        )
        self.trigger_specs.append(
            {
                "name": name,
                "table_key": f"{table_schema}.{table_name}",
                "timing": timing,
                "events": events,
                "body": match.group(5).strip(),
                "definition": statement,
            }
        )

    def _attach_triggers(self) -> None:
        for spec in self.trigger_specs:
            table = self.tables.get(spec["table_key"])
            if table is None:
                continue
            table.triggers.append(
                Trigger(
                    name=spec["name"],
                    timing=spec["timing"],
                    events=spec["events"],
                    function=spec["name"],
                    definition=spec["definition"],
                    body=spec["body"],
                    row_level=True,
                    provenance=self._prov(spec["definition"], "trigger"),
                )
            )

    # ------------------------------------------------------------ relationships
    def _infer_relationships(self) -> None:
        for table in list(self.tables.values()):
            for column in table.columns:
                if not column.name.endswith("_id"):
                    continue
                if any(column.name in fk.child_columns for fk in table.foreign_keys):
                    continue
                base = column.name[: -len("_id")]
                candidates = {base, f"{base}s", f"{base}es"}
                if base.endswith("y"):
                    candidates.add(f"{base[:-1]}ies")
                for candidate in self.tables.values():
                    if candidate.name not in candidates or len(candidate.primary_key) != 1:
                        continue
                    parent_column = candidate.primary_key[0]
                    try:
                        parent = candidate.column(parent_column)
                    except KeyError:
                        continue
                    if not _compatible(column, parent):
                        continue
                    table.foreign_keys.append(
                        ForeignKey(
                            name=f"{table.name}_{column.name}_fkey",
                            child_schema=table.schema,
                            child_table=table.name,
                            child_columns=[column.name],
                            parent_schema=candidate.schema,
                            parent_table=candidate.name,
                            parent_columns=[parent_column],
                            inferred=True,
                            provenance=Provenance(
                                "inferred", "naming", f"{table.key}.{column.name}"
                            ),
                        )
                    )
                    break

    # ---------------------------------------------------------------- routines
    def _analyze_routines(self) -> None:
        for function in self.functions:
            body = function.body or ""
            function.conditions = [
                " ".join(match.group(1).split())
                for match in re.finditer(
                    r"\bIF\s+(.+?)\s+(?:BEGIN|THROW|RAISERROR)\b",
                    body,
                    re.IGNORECASE | re.DOTALL,
                )
            ]
            function.statements = [
                match.group(1).replace("''", "'")
                for match in re.finditer(
                    r"\b(?:EXEC(?:UTE)?|sp_executesql)\s*(?:\(\s*)?N?'((?:[^']|'')*)'",
                    body,
                    re.IGNORECASE | re.DOTALL,
                )
            ]
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
                    for match in re.finditer(
                        rf"\bFROM\s+({_QUALIFIED})", body, re.IGNORECASE
                    )
                }
            )

    def _extract_rules(self) -> None:
        for table in self.tables.values():
            for trigger in table.triggers:
                body = trigger.body or ""
                if "THROW" not in body.upper() and "RAISERROR" not in body.upper():
                    continue
                for match in re.finditer(
                    r"\bIF\s+(.+?)\s+(?:BEGIN|THROW|RAISERROR)\b",
                    body,
                    re.IGNORECASE | re.DOTALL,
                ):
                    condition = " ".join(match.group(1).split())
                    expression = self._negate_tsql(condition)
                    if expression is None:
                        continue
                    self.rules.append(
                        RuleCandidate(
                            id=f"rule:{table.key}:{len(self.rules) + 1}",
                            kind="guard",
                            table=table.key,
                            expression=expression,
                            confidence=0.6,
                            applied=False,
                            provenance=Provenance(
                                "trigger", trigger.name, table.key
                            ),
                            note=(
                                f"IF {condition} THEN THROW/RAISERROR "
                                "(segmentation only)"
                            ),
                        )
                    )
        trigger_by_name = {spec["name"]: spec for spec in self.trigger_specs}
        for function in self.functions:
            source = trigger_by_name.get(function.name)
            table = source["table_key"] if source else ""
            provenance = Provenance(
                "trigger" if source else "function",
                function.name,
                f"{function.schema}.{function.name}",
            )
            for condition in function.conditions:
                if "THROW" not in function.body.upper() and "RAISERROR" not in function.body.upper():
                    continue
                expression = self._negate_tsql(condition)
                if expression is None:
                    continue
                self.rules.append(
                    RuleCandidate(
                        id=f"rule:{table or 'routine'}:{len(self.rules) + 1}",
                        kind="guard",
                        table=table,
                        expression=expression,
                        confidence=0.6,
                        applied=False,
                        provenance=provenance,
                        note=f"IF {condition} THEN THROW/RAISERROR (segmentation only)",
                    )
                )

    def _negate_tsql(self, condition: str) -> str | None:
        text = condition.strip()
        text = re.sub(r"^\(+|\)+$", "", text).strip()
        flip = {"<": ">=", "<=": ">", ">": "<=", ">=": "<", "=": "<>", "<>": "=", "!=": "="}
        match = re.match(r"^(.+?)\s*(<=|>=|<>|!=|=|<|>)\s*(.+)$", text, re.DOTALL)
        if match and match.group(2) in flip:
            return f"{match.group(1).strip()} {flip[match.group(2)]} {match.group(3).strip()}"
        return None


def load_dump(path: str | Path) -> Catalog:
    return SqlServerDumpLoader().load(Path(path).read_text(encoding="utf-8"))


__all__ = ["SqlServerDumpLoader", "load_dump", "split_batches"]
