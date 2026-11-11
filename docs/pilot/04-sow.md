# Statement of work — two-week pilot

## Parties

- **Provider:** {nombre / razón social}, RFC {rfc}.
- **Client:** {razón social}, RFC {rfc}.
- **Effective date:** {fecha}.

## Objective

Produce a synthetic dataset for the client's PostgreSQL staging environment that
satisfies the agreed business invariants, with a reproducible verification
report.

## Scope

Included:

1. Schema audit and baseline report.
2. Agreement on three to five business invariants.
3. Generation of a synthetic dataset into a staging database owned by the client.
4. Repair statements for invariants that can be repaired deterministically.
5. Verification report in JSON, HTML and JUnit XML.
6. CI integration with `--fail-on never` during the pilot.
7. A runbook and a 60-minute handover session.

Not included:

- Production data migration.
- Performance tuning of the client's database.
- Application code changes beyond the CI step.
- Legal or tax advice.
- Support outside the two-week window.

## Deliverables

| Deliverable | Format | Due |
|---|---|---|
| Schema audit | Markdown | Day 1 |
| Agreed invariants | `plan.yml` | Day 3 |
| First PASS report | JSON + HTML | Day 6 |
| CI integration | Pull request | Day 8 |
| Final report and runbook | Markdown + reports | Day 9 |
| Handover session | 60 minutes | Day 10 |

## Client responsibilities

- Provide read-only access to the schema or a dump within one business day.
- Provide a non-production target database the provider may truncate.
- Name one technical owner who can answer questions within 24 hours.
- Keep the target database isolated from production.

## Provider responsibilities

- Run everything inside the client's environment. No schema or data leaves it
  without written permission.
- Deliver the artifacts in the client's repository or a shared folder.
- Document every limitation in the report.
- Do not use client data to train models or build benchmarks.

## Price and payment

- Pilot fee: **{15,000–25,000} MXN + IVA**.
- 50% on signature, 50% on delivery.
- Payment by SPEI. Invoice (CFDI) issued at each payment.
- The pilot fee is credited toward the first three months of a monthly plan.

## Guarantee

If the agreed invariants do not reach zero violations by day 10, the client pays
nothing and keeps the audit and the report. The provider keeps the learning.

## Confidentiality and data

- The provider signs the client's NDA if required.
- Schema metadata is treated as confidential.
- The client's data processing agreement applies to any personal data.
- The provider deletes local copies of dumps within 15 days of delivery.

## Intellectual property

- smoker remains the provider's property.
- The `plan.yml`, the runbook and the reports belong to the client.
- The provider may reference the engagement anonymously ("a fintech with 40
  tables") unless the client authorizes a named case study.

## Acceptance

The pilot is accepted when:

1. The dataset loads without errors.
2. Every agreed invariant reports zero violations.
3. The client can rerun the pipeline with the documented command.

## Signatures

Provider: ______________________  Date: __________

Client: ________________________  Date: __________
