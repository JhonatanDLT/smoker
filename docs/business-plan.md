# Business plan — pgproof

**Status:** working draft, November 2026.
**Product:** synthetic Postgres data with a verifiable certificate.
**Founder:** solo, part-time, Mexico. Infrastructure: one CPU-only VPS, ~400 MXN/month.

---

## 1. Thesis

The synthetic data market is crowded at both ends: free libraries that generate
plausible rows, and enterprise platforms that sell compliance to large
organizations. The middle is empty and it is where the money is:

> **Teams that need provably valid test data, without a six-figure contract.**

pgproof does not sell "realistic data". It sells **a dataset that loads, plus a
report that proves it**. The generator is the means; the certificate is the
product.

**Positioning line:** *Synthetic Postgres data that ships with proof.*

---

## 2. Why now

Three forces make this timely:

1. **Postgres won.** It is the default database for new applications, and the
   destination of most legacy migrations.
2. **Compliance tightened.** LFPDPPP (Mexico, March 2025) and GDPR make
   production data in development a legal liability, not just a bad practice.
3. **The category leaders died or moved upmarket.** Snaplet closed in August
   2024. Neosync was acquired and its repository archived in August 2025.
   Tonic survives at $18–28k/year, selling to compliance departments.

The gap: a developer-first tool that a single engineer can install in two
minutes and that a reviewer can trust.

---

## 3. Market

| Market | 2025–26 | CAGR | Relevance |
|---|---|---|---|
| Synthetic data generation | $0.60–0.79B | 35–39% | Inflated by AI training, not testing |
| **Test Data Management** | **$1.58–1.71B** | 11–16% | **The real budget** |
| Application modernization services | $22.7–26.4B | 15–18% | Highest-ACV wedge |

- **TAM:** ~$1.7B (TDM + testing-oriented synthetic tooling).
- **SAM:** ~$200M — organizations running Postgres in production, >20
  developers, with compliance pressure. Assumption: 25,000 organizations ×
  $8k ACV.
- **SOM at 18 months:** $90–250k ARR. Enough for a solo founder, not enough for
  a venture round. That is the point.

**Honest caveat:** the ACV in pure Postgres self-serve is 5–20× lower than in
Oracle/SQL Server enterprises. The high-ACV entry is **migration validation**,
not subscription revenue.

---

## 4. Customer

### The three buyers

| # | Buyer | Pain | Budget | Cycle |
|---|---|---|---|---|
| 1 | **Migration teams** (Postgres upgrades, ORM changes, monolith splits) | Cannot prove the new schema survives real data | **15–35k MXN per project** | 2–6 weeks |
| 2 | **Fintech / SaaS with fiscal data** (CFDI, LFPDPPP) | Real client data in dev is a regulatory risk | $3–15k/year | 2–6 weeks |
| 3 | **Agencies and consultancies** | Need a staging dataset per client | 2–6k MXN/month | days |

Enterprise QA in banking and insurance pays the most ($30–150k/year) but is not
reachable in 90 days: they run Oracle and SQL Server, and procurement is slow.

### Ideal customer profile (first 20)

- 10–200 engineers, Postgres 14–17 in production.
- At least one of: a migration in the next two quarters, an audit, or a
  multi-tenant staging problem.
- The buyer is a tech lead or platform engineer who can approve $29–500/month
  without procurement.

### The trigger events

1. An audit asks how production data reaches non-production.
2. A major migration is scheduled.
3. A bug cannot be reproduced locally.
4. A new client requires an isolated UAT environment.
5. The previous tool disappeared (Snaplet, Neosync).

---

## 5. Competition

| Player | Price | Gap |
|---|---|---|
| Tonic.ai | $29/mo starter; $18–28k/year enterprise | Sales-led, weak on triggers and procedures |
| Gretel (Nvidia, ~$320M) | Free / $295 / custom | AI training data, not schema integrity |
| Delphix / K2View | $100k+ | Appliances, 9–12 month cycles |
| Neosync | Archived Aug 2025 | — |
| SDV | Free | No database integration |
| Mockaroo | $60/year | No schema awareness |
| Redgate SQL Data Generator | $303/user/year | SQL Server only |
| Seedfast | $0 / $16 / $69 | AI seeding, no formal verification |

**The gap:** nobody sells *"provably insertable"*. Nobody generates data that
satisfies checks, exclusion constraints, domains, generated columns, triggers
and procedures — and then proves it with a reproducible report.

---

## 6. Product

### v1 scope

- PostgreSQL 14–17.
- Introspection of tables, columns, PK/FK/unique/not-null, enums, domains,
  simple checks.
- Deterministic generation with `COPY`, seeded and reproducible.
- Local enforcement of ranges, `IN (...)`, arithmetic derivations
  (`amount = quantity * unit_price`) and column orderings (`ends_at > starts_at`).
- YAML spec for application-level rules.
- Verification report in JSON and HTML.
- CLI, `pipx` install, Docker image.

### Explicitly out of scope for v1

Other engines, web UI, SaaS hosting, authentication, billing, masking production
dumps, cross-table business rules, CDC, ML-based synthesis.

### The five-minute demo

1. Generate 400k rows against a 14-table billing schema.
2. Verify: 10/10 invariants green.
3. Run a naive Faker seed on the same schema: **23% of rows violate something**.
4. Show the offending row and the query that catches it.
5. Re-run with the same seed: identical hash.

The "wow" is catching data that looks plausible but is wrong.

---

## 7. Business model

**Open core + license + pilots.** The CLI is distribution, not the product.

| Plan | Price | Includes |
|---|---|---|
| **Free (CLI)** | $0 | Local, unlimited, no account, watermarked report |
| **Indie** | $29/month | CI integration, signed reports, 30-day history |
| **Team** | $499/month | SSO, shared configs, report archive, SLA |
| **On-prem (regulated)** | $12–25k/year | From month 9, only on demand |
| **Migration pilot** | 15–35k MXN | 2–3 weeks, includes an audit-ready report |

Avoid the $150–300 dead zone: too expensive for a developer, too cheap to fund
sales.

Never charge per generation in self-serve: developers iterate twenty times per
afternoon and a meter feels like a taxi.

---

## 8. Go-to-market

| Channel | Clients | Cost | Time |
|---|---|---|---|
| Own network + LatAm fintech/SaaS | 4 | $0 | days |
| **GitHub outbound** (repos with Postgres + Prisma/Alembic/dbt) | 5 | $0 | 1–2 weeks |
| Launch on HN / r/PostgreSQL / Postgres Weekly | 5 | $0 | 1 week, spike |
| Migration consultancies (partner) | 3 | $0 | 2–6 weeks |
| Development agencies (staging per client) | 3 | $0 | 2–4 weeks |
| Long-tail SEO | — | $0 | 3–6 months |

**Entry offer:** *"Two-week pilot: I generate a synthetic dataset for your schema
that passes your constraints and five business invariants, with a verification
report. If it does not reach zero violations, you do not pay."*

**Lead magnet:** a one-page schema audit — tables, foreign keys, candidate
invariants, and risks. Done by hand for the first ten prospects.

**What never to ask for:** email before install, a card to try it, uploading the
schema to our cloud, or a scheduled demo.

---

## 9. Financials

**Fixed costs:** VPS 400 MXN + domain ~25 + AI ~100 + tools ~300 + payment fees
≈ **1,000 MXN/month**.

**Break-even:** one pilot per quarter, or two Indie subscriptions.

| Scenario | D90 | D180 | M12 | M18 |
|---|---|---|---|---|
| Pessimistic | 1 payment | 3 clients · 8k MRR | 8 · 15k | 15 · 30k |
| **Base** | 3 payments · 10k | 10 · 25k | 22 · 60k | **35 · 120k MXN** |
| Optimistic | 8 payments · 30k | 20 · 60k | 50 · 180k | 90 · 350k |

Gross margin ~90%. The constraint is not margin; it is support time. Hard cap:
**one hour per client per week**.

---

## 10. Risks

| Risk | Probability | Mitigation |
|---|---|---|
| Self-serve does not convert (Neosync/Snaplet pattern) | **High** | High-ACV pilots and on-prem license; never depend on $29 |
| "Faker is enough" | High | Reposition to "datasets that do not break CI"; demo the 23% failure |
| Distrust of uploading a schema | High | 100% local CLI, `--offline`, security page, offline license |
| A funded competitor copies 70% in 9–12 months | Medium | Invariant corpus + niche + speed, not technology |
| Verifier complexity explodes | High | v1 only handles simple constraints; the report states limits |
| Founder is the bottleneck | High | Max 5 concurrent onboardings; docs before features |
| Low ACV in pure Postgres | Medium | Sell migration validation, not cheap subscriptions |

**Real moat:** the versioned invariant corpus per schema, the verification
oracle, and the reproducible report embedded in CI. Not the LLM, not the parser,
not the fake-data library.

---

## 11. Execution plan

| Phase | Days | Goal | Exit criterion |
|---|---|---|---|
| F0 Discover | 1–14 | Pick one use case | 10 interviews, 3 design partners |
| F1 Core | 15–42 | Generate and load without violations | 3 real schemas pass |
| F2 Certify | 43–75 | Turn "generate" into "certify" | 20 schemas, 2 paying |
| F3 CI | 76–105 | Runs without the founder | 5 clients, activation ≥40% |
| F4 SQL Server | 106–130 | Second dialect, only with paid demand | 2 written requests |
| F5 Sales | 131–160 | Repeatable sale | 9 clients, 25k MRR |
| F6 Decide | 161–180 | Decide with data | Written scale/pivot/kill |

### Decision rules

- **Accelerate:** 3 payments by D60 and activation ≥50% → raise price, double
  outbound.
- **Pivot the wedge, not the technology:** by D90 with fewer than 2 payments →
  move to migration validation as a pure service, 30-day test.
- **Kill:** by D120 with fewer than 3 payments, fewer than 5 pilots, or CAC
  above 3 months of MRR.

---

## 12. KPIs

1. Real schemas processed end to end (target: 20 by D90).
2. Dataset load success rate (target: ≥90%).
3. Activation: trials reaching a certified dataset (target: ≥50%).
4. Payments received, not promised.
5. Support hours per client per week (cap: 1).

---

## 13. Why this can work for one person

- The infrastructure cost is $22/month; the break-even is one client.
- The first sale is a service, not a product: no need to build the SaaS first.
- The engine is pure Python with no external dependencies at runtime.
- The moat compounds with every schema: each one adds rules, edge cases and
  fixtures to the corpus.

The failure mode is not technical. It is spending six months building a
beautiful generator for a market that only pays for the certificate.
