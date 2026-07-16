# Security One-Pager for EIS / Information Security

> **SYNTHETIC DATA ONLY** (accelerator). For: Arun (EIS/security) and the security review team.
> Purpose: answer the three questions security always asks about a data catalog + de-id pattern.

## TL;DR

- **Cataloging data ≠ exposing data.** The OneLake Catalog is **discovery-only**: it shows
  *names and metadata* so people can find datasets. It does **not** grant access to the bytes.
- **Access to data is enforced separately** by **OneLake security** (data-access roles /
  RLS / CLS) — a *data-plane* control, scoped per-role, **not** "tenant-wide."
- **De-identification physically removes PHI** before the analytics/AI layer, so the data
  most people touch is not PHI at all.

## Q1 — "If we put everything in the catalog, is our PHI now discoverable tenant-wide?"

No. Two different planes:

| Plane | What it governs | Feature | "Tenant-wide"? |
|-------|-----------------|---------|----------------|
| **Control plane** | *Finding & describing* items | OneLake Catalog, Purview labels, domains | Findability of **names/metadata** only |
| **Data plane** | *Reading the bytes* | OneLake security roles, RLS, CLS | **No** — role-scoped, least-privilege |

Appearing in the catalog leaks no rows, no columns, no values. A user who finds a dataset
still cannot open it unless a data-access role grants them the data plane.

## Q2 — "How is access actually enforced, not just labeled?"

Three layers, defense-in-depth:

1. **De-identification (primary).** Notebooks `02b`/`03b` transform Silver→Gold so the Gold
   layer (`gold_safe_*`) contains **zero** HIPAA Safe Harbor identifiers. Most consumers
   only ever see de-identified data → the blast radius of any access mistake is minimized.
2. **OneLake security (data plane).** Data-access roles decide who can read which
   tables/folders; RLS/CLS scope rows/columns further. This is enforcement, not metadata.
3. **Workspace isolation.** Three-workspace model (below) keeps raw PHI and the
   re-identification crosswalk away from analysts entirely.

> A Purview **sensitivity label is a classification stamp, not an access control.** Labeling
> a column "PHI" describes it; OneLake security is what stops someone reading it.

## Q3 — "Where does the raw PHI live, and who can re-identify?"

Three-workspace isolation:

| Workspace | Contents | Who (approx.) |
|-----------|----------|---------------|
| **Raw** | Bronze/Silver with raw PHI; the de-id notebooks | ~3 platform/data engineers |
| **Analytics** | `gold_safe_*` (PHI-free), semantic model, Power BI, Copilot | Analysts, business users |
| **Vault** | Re-identification crosswalk (`xwalk_*`), `NB_reidentify` | ~2 authorized approvers |

- The **tokenization pepper** lives in **Azure Key Vault**, never in code, tables, notebook
  output, or Git. Rotating it invalidates every token (breach-recovery lever).
- **Re-identification** is HMAC-irreversible by math; it is only possible via the Vault
  crosswalk, gated by OneLake security + audit logging. ~2 people, every use logged.
- The de-id notebook is the **single privileged crossing point** and enforces a hard rule:
  no `display()/show()/print()/collect()/toPandas()` of raw or crosswalk columns (checked in
  `tests/`), so raw values can't leak into notebook output or logs.

## Certifications & shared responsibility

- Fabric runs on Azure, is **in-scope under the Microsoft BAA**, and carries certifications
  (ISO 27001, HITRUST, SOC) available via the **Service Trust Portal**.
- "HIPAA compliant" is **not a product checkbox** — it is **shared responsibility**. Microsoft
  secures the platform; the customer secures configuration, access, and process. This
  accelerator is a **reference pattern**, not a compliance certification. See
  [../docs/hipaa_compliance.md](../docs/hipaa_compliance.md).

## What we need from EIS

- Confirm Entra security groups for the three workspaces and the analyst/steward roles.
- Approve the Key Vault + pepper-rotation process.
- Approve audit-log routing for `NB_reidentify` usage.
