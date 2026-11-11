# Pilot playbook

This folder is the commercial kit for the first paid pilots. It is written to be
used, not admired: copy the templates, fill the placeholders, send the messages.

## The offer

> **Two weeks. Five business invariants. Zero violations, or you do not pay.**

Price: **15,000–25,000 MXN**, 50% upfront.

## Two-week timeline

| Day | What happens | Deliverable |
|---|---|---|
| 0 | Prospect replies. 20-minute call. | Qualification and next step |
| 1 | Read-only access or a schema dump. | Baseline `smoker audit` |
| 2–3 | Agree on 3–5 business invariants. | Signed one-page scope |
| 4–6 | Generate into staging, add repairs. | First PASS report |
| 7–8 | Integrate into CI with `--fail-on never`. | Pipeline running |
| 9 | Final report, runbook, handover. | `report.json`, `report.html`, `runbook.md` |
| 10 | Retro and conversion conversation. | Proposal for monthly plan |

## What the client receives

- `audit.md` — schema readiness audit.
- `report.json`, `report.html`, `report.xml` — verification evidence.
- `plan.yml` — the invariants and repairs, committed to their repo.
- `runbook.md` — how to rerun it, what each invariant means, who owns it.
- A 60-minute handover session.

## Files

| File | Use |
|---|---|
| `01-prospect-list.md` | Who to contact and how to qualify |
| `02-outreach.md` | Messages and call script |
| `03-audit-template.md` | The one-page lead magnet |
| `04-sow.md` | Statement of work |
| `05-pricing.md` | Price, payment and conversion |
| `06-metrics.md` | Funnel and weekly review |
| `07-legal.md` | NDA, data processing, invoicing |
| `prospects.csv` | Tracking template |

## The one rule

**Sell the report, not the generator.** The client is buying evidence that their
staging data is safe and correct. The CLI is how you produce it.
