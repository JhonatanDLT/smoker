# Legal checklist

This is an operational checklist, not legal advice. Have a lawyer review the
final documents.

## Before the first call

- [ ] NDA template ready (mutual, one page).
- [ ] Data processing agreement template ready.
- [ ] Privacy notice for the provider's own site and invoices.
- [ ] RFC and fiscal regime confirmed with the accountant.

## Before accessing a schema

- [ ] NDA signed if the client requires it.
- [ ] Written authorization to read schema metadata.
- [ ] Preferred: the client runs `pgproof introspect` and sends the JSON.
- [ ] If a dump is shared: encrypted transfer, local copy deleted in 15 days.

## During the pilot

- [ ] No production data is copied to the provider's machines.
- [ ] The target database is non-production and owned by the client.
- [ ] The client confirms in writing that the target may be truncated.
- [ ] Every report states what was not verified.

## Invoicing

- [ ] CFDI 4.0 issued per payment.
- [ ] Payment method and reference documented.
- [ ] Contract references the invoice and the statement of work.

## Data protection (México)

- LFPDPPP applies if personal data is processed, even in test environments.
- The provider acts as *encargado* for any personal data in the client's schema.
- Keep a record of processing activities.
- Do not use client data to train models.
- Delete local copies on request and at the end of the engagement.

## Contract clauses worth having

1. Scope and deliverables, with dates.
2. Payment terms and refund condition.
3. Confidentiality and data handling.
4. Limitation of liability: the report is evidence, not a legal certification.
5. No guarantee of regulatory compliance; the client remains responsible before
   the authority.
6. Ownership of artifacts: `plan.yml` and reports belong to the client.
7. Termination: either party with 7 days' notice before the pilot starts.

## What never to say

- "Cumplimiento garantizado."
- "Certificado ante el SAT."
- "100% seguro."
- "Reemplaza a tu auditor."

Say instead: "evidencia reproducible de que el dataset cumple las invariantes
acordadas, con límites explícitos."
