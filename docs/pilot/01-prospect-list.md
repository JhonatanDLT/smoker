# Prospect list

## Ideal customer profile

**Must have**

- PostgreSQL 14–17 in production.
- 10–200 engineers, or an agency managing 5+ client databases.
- One of these triggers: a migration in the next two quarters, an audit
  (LFPDPPP, SOC 2, GDPR), a new UAT environment, or a staging database that is a
  copy of production.

**Nice to have**

- Recent funding, hiring backend or data engineers, or a public postmortem about
  a data incident.
- A public repo with Postgres and Prisma, Alembic, Drizzle or dbt.
- Fiscal data (CFDI) or health data, where using production rows in dev is a
  regulatory problem.

**Disqualify**

- MySQL or SQL Server only. Not supported yet.
- Fewer than 10 tables. Faker is probably enough.
- No one who can approve 15,000 MXN without a long procurement cycle.
- "We copy production and it's fine." They do not feel the pain yet.

## Four segments

| Segment | Where to find them | Hook |
|---|---|---|
| **Startups / scaleups** with Postgres | LinkedIn, Wellfound, local VC portfolio pages, GitHub | "You are migrating or hiring; staging is about to become a problem." |
| **Fintech / SaaS with fiscal data** | CFDI/PAC ecosystem, accounting-software vendors, LatAm fintech lists | "Real client data in dev is an LFPDPPP risk." |
| **Agencies / consultancies** | Clutch, LinkedIn, local dev communities | "One staging dataset per client, without copying production." |
| **Migration teams** | Job posts for "Postgres migration", "Oracle to Postgres", "database modernization" | "Prove the new schema survives real data before cutover." |

## Research checklist per prospect

Spend 10 minutes, no more:

1. What database do they use? (job posts, stack pages, GitHub)
2. How many tables does their public schema have? (if a repo is public)
3. What is the trigger? (migration, audit, funding, hiring, incident)
4. Who owns the database? (tech lead, platform engineer, CTO)
5. What is the specific finding you can show them?

## 40-slot tracker

Use `prospects.csv`. Columns:

```
company,segment,contact_name,role,email,linkedin,trigger,finding,status,next_action,next_date,notes
```

Status values: `new`, `contacted`, `replied`, `call`, `pilot`, `won`, `lost`,
`nurture`.

## Qualification questions (20-minute call)

1. What database and version are you on?
2. How do you populate staging today?
3. What breaks when staging data is wrong or stale?
4. Is there a migration, audit or new environment in the next two quarters?
5. Who else needs to approve a 15–25k MXN pilot?
6. If we reach zero violations in two weeks, what happens next?

If the answer to 4 is "no" and to 3 is "nothing", disqualify politely.

## Weekly target

| Activity | Target |
|---|---|
| New prospects researched | 10 |
| Messages sent | 10 |
| Replies | 2–3 |
| Calls | 1–2 |
| Pilots proposed | 1 |
