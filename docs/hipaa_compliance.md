# Is Microsoft Fabric "HIPAA compliant"? — Shared Responsibility

> **SYNTHETIC DATA ONLY** (accelerator). This document is **not legal advice** and **not** a
> compliance certification. Confirm specifics with Microsoft licensing/compliance and your
> own privacy/legal team.

## The short answer

**"HIPAA compliant" is not a product checkbox.** No cloud service is "HIPAA compliant" on
its own. Fabric is **HIPAA-capable**: it runs on Azure, is **in-scope under the Microsoft
Business Associate Agreement (BAA)**, and carries independent certifications you can retrieve
from the **Service Trust Portal** (e.g. ISO 27001, HITRUST, SOC 1/2). Compliance is achieved
by **how you configure and operate** the platform — this is **shared responsibility.**

## Who is responsible for what

| Microsoft (the platform) | The customer (the covered entity) |
|--------------------------|-----------------------------------|
| Physical + infrastructure security | Access control configuration (who reads what) |
| Platform certifications (ISO/HITRUST/SOC) | Data classification & handling |
| Offering Fabric under the BAA | Signing/activating the BAA; keeping in-scope |
| Encryption at rest/in transit | Key management (e.g. the tokenization pepper) |
| Audit-log capability | Reviewing logs; incident response |
| — | **De-identification** (there is no native Fabric feature for it) |

## Why de-identification changes the game

Once data is **de-identified** — all 18 Safe Harbor identifiers removed/generalized, or an
Expert Determination obtained — it is **no longer PHI** under HIPAA, so it falls **outside**
HIPAA's use/disclosure constraints. That is the accelerator's core value:

- PHI in Raw/Silver stays inside the tightly controlled workspace under the BAA.
- The de-identified `gold_safe_*` layer can flow to **analytics, self-service BI, and AI /
  Copilot** without BAA-scoped restrictions, because it isn't PHI anymore.

> De-identification is what lets the customer say "yes" to AI on this data without every
> project inheriting full HIPAA scope.

## What this accelerator does and does not claim

- **Does:** demonstrate a defensible *pattern* to produce de-identified data in-tenant, plus
  a scorecard that checks the output.
- **Does NOT:** certify that your data is HIPAA de-identified, provide an Expert
  Determination, or constitute legal advice. Those require your own validation and, for
  Expert Determination, a qualified statistician.

## Practical checklist before real PHI

- [ ] BAA is in place and Fabric usage is in-scope.
- [ ] Security review completed; three-workspace isolation implemented.
- [ ] Key Vault holds the pepper; rotation process approved.
- [ ] `deid_rules.yaml` validated against the real schema by a qualified reviewer.
- [ ] All 18 Safe Harbor identifiers addressed **or** Expert Determination obtained.
- [ ] `NB_scorecard` passes; audit logging enabled for `NB_reidentify`.
- [ ] Certifications retrieved from the Service Trust Portal for your compliance file.
