# Schema audit template

The audit is the lead magnet: one page, no production rows read, produced in
minutes.

## Generate it

```bash
export SMOKER_SOURCE_DSN=postgresql://readonly@host/db
smoker audit --dsn-env SMOKER_SOURCE_DSN --out audit.md
```

If the client cannot give a connection, ask for a schema dump and load it into a
local Postgres, then run the same command against the local copy.

## Structure

1. **Header** — database, PostgreSQL version, verdict (high / medium / low risk).
2. **Inventory** — tables, columns, foreign keys, checks, triggers.
3. **What smoker would verify** — counts by invariant kind.
4. **Risks** — severity, table, finding. This is the part the buyer reads.
5. **Next step** — the two-week pilot.

## What counts as a finding

| Severity | Finding | Why it matters |
|---|---|---|
| High | Table without a primary key | Rows cannot be referenced, deduplicated or repaired safely |
| High | Tenant column without a foreign key | Cross-tenant leaks in generated or copied data |
| Medium | Table without foreign keys | Orphan rows are possible and invisible |
| Medium | Trigger with side effects | Test data does not reproduce audit tables or denormalization |
| Medium | Check the generator cannot parse | It will be enforced by PostgreSQL at load time, not pre-validated |
| Medium | `NOT VALID` constraint | Existing rows were never checked |
| Low | Nullable foreign key | Cardinality assumptions break |
| Low | Wide table (>40 columns) | Review and test cost grows |

## Delivery email

> {nombre}, adjunto la auditoría de su esquema. Tres hallazgos:
>
> 1. {hallazgo de severidad alta}
> 2. {hallazgo de severidad media}
> 3. {hallazgo de severidad media}
>
> No leí datos: todo sale del catálogo. Si quiere, en 20 minutos revisamos qué
> tomaría llegar a cero violaciones en staging.

## Rules

- Never invent a finding. If the audit is clean, say so and disqualify.
- Never send raw schema names that are confidential without asking.
- Do not attach the full catalog JSON; the Markdown is enough.
- Regenerate the audit the day you send it; schemas change.
