"""AuditSchema use case: assess a schema without touching data."""

from __future__ import annotations

from ..core.audit import Audit, Risk
from ..core.dialect import SqlDialect
from ..core.invariants import invariants_from_catalog, parse_local_rules
from ..core.ir import Catalog


class AuditSchema:
    def __init__(self, catalog: Catalog, dialect: SqlDialect) -> None:
        self.catalog = catalog
        self.dialect = dialect

    def execute(self) -> Audit:
        risks: list[Risk] = []
        foreign_keys = 0
        checks = 0
        triggers = 0

        for table in self.catalog.tables:
            checks += len(table.checks)
            triggers += len(table.triggers)
            foreign_keys += len(table.foreign_keys)

            if not table.primary_key:
                risks.append(
                    Risk(
                        "high",
                        table.key,
                        "No primary key: rows cannot be referenced or deduplicated.",
                    )
                )
            if len(table.columns) > 40:
                risks.append(
                    Risk(
                        "low",
                        table.key,
                        f"Wide table ({len(table.columns)} columns): review and test cost grows.",
                    )
                )
            if not table.foreign_keys and len(table.columns) > 6:
                risks.append(
                    Risk(
                        "medium",
                        table.key,
                        "No foreign keys: orphan rows are possible and invisible.",
                    )
                )

            for fk in table.foreign_keys:
                for name in fk.child_columns:
                    column = table.column(name)
                    if column.nullable:
                        risks.append(
                            Risk(
                                "low",
                                table.key,
                                f"Nullable foreign key {name} -> {fk.parent_key}.",
                            )
                        )

            for trigger in table.triggers:
                risks.append(
                    Risk(
                        "medium",
                        table.key,
                        f"Trigger {trigger.name} ({trigger.events}): side effects are "
                        "inventoried but not generated.",
                    )
                )

            rules = parse_local_rules(table)
            for name in rules.unparsed:
                risks.append(
                    Risk(
                        "medium",
                        table.key,
                        f"Check {name} is not parsed by the generator; PostgreSQL "
                        "enforces it at load time.",
                    )
                )
            for check in table.checks:
                if not check.validated:
                    risks.append(
                        Risk(
                            "medium",
                            table.key,
                            f"Constraint {check.name} is NOT VALID on existing rows.",
                        )
                    )

        invariants = invariants_from_catalog(self.catalog, self.dialect)
        counts: dict[str, int] = {}
        for invariant in invariants:
            counts[invariant.kind.value] = counts.get(invariant.kind.value, 0) + 1

        functions = sum(
            1 for routine in self.catalog.functions if routine.kind != "procedure"
        )
        procedures = sum(
            1 for routine in self.catalog.functions if routine.kind == "procedure"
        )
        if procedures:
            risks.append(
                Risk(
                    "low",
                    "database",
                    f"{procedures} stored procedures are inventoried but not executed.",
                )
            )
        return Audit(
            database=self.catalog.database,
            server_version=self.catalog.server_version,
            tables=len(self.catalog.tables),
            columns=sum(len(table.columns) for table in self.catalog.tables),
            foreign_keys=foreign_keys,
            checks=checks,
            triggers=triggers,
            dialect=self.dialect.name,
            functions=functions,
            procedures=procedures,
            invariants=counts,
            risks=risks,
        )
