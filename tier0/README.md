# Tier 0 · Catalog Enablement & Classification

> **SYNTHETIC DATA ONLY.** Runbook for the foundational governance tier.

Tier 0 is the **"walk"** before the Tier 3 **"run."** Most of it is available today and is
mostly UI + light automation. It answers the customer's actual first question — *"can our
people find and understand our data, and can security trust how it's classified?"* — and it
produces the classification that **feeds the de-identification engine** in Tier 3.

**PG positioning (new customers):** lead with **Microsoft Fabric as Microsoft's primary
data governance solution**, and the **OneLake catalog as its unified governance
foundation** — one place to **discover, manage, and govern** data across **multi-cloud and
hybrid** environments. Tier 0 *is* that OneLake-catalog starting point; everything below
builds on it.

## What Tier 0 delivers

| Capability | Fabric feature | Who |
|-----------|----------------|-----|
| Discover, manage & govern data (multi-cloud & hybrid) | **OneLake Catalog** — unified governance foundation (discovery-only) | Business users, analysts, stewards |
| Classify sensitive columns | **Purview sensitivity labels** + info-protection scanning | Data stewards |
| Organize by business area | **Domains** & workspaces | Governance team |
| Enforce who reads which bytes | **OneLake security** (RLS/CLS/data-access roles) | Platform admins |
| Monitor & audit | **Purview hub**, activity/audit logs | Security (EIS) |

## The Tier-0 → Tier-3 bridge

The sensitivity classification you assign here is not busywork — it is the **source of the
rulebook**. Every column you tag as PHI/PII in the catalog maps to a strategy in
[`config/deid_rules.yaml`](../config/deid_rules.yaml). The taxonomy is intentionally shared:

```
Catalog label            ->  deid_rules.yaml strategy
------------------------     -------------------------------
Direct identifier (MRN)  ->  tokenize
Name (PII)               ->  synthesize
Date of birth / service  ->  generalize(year) | date_shift
Geography (ZIP)          ->  generalize(zip3)
Age                      ->  generalize(age_cap=90)
Non-sensitive            ->  passthrough
Unclassified             ->  suppress  (deny-by-default)
```

**Message to the customer:** *the classification you do in the catalog today becomes the
rulebook that de-identifies your data tomorrow.*

## Runbook

1. **Land & register.** Confirm the Lakehouse/Warehouse items appear in the OneLake
   Catalog. Discovery is metadata-only — appearing in the catalog leaks **no** underlying
   data (see the EIS one-pager).
2. **Domains.** Create business domains (e.g. *Provider Analytics*) and assign workspaces so
   discovery is organized and ownership is clear.
3. **Classify.** Apply Purview sensitivity labels to columns. Start with the PHI columns in
   `dim_patient`, `dim_provider`, `dim_provider_credential`, `dim_facility`.
4. **Inventory.** Run [`inventory_catalog.py`](inventory_catalog.py) to export a
   machine-readable inventory of items + (where available) classifications for review.
5. **Enforce.** Define OneLake security data-access roles so each audience reads only what
   it should. This is the data-plane control (Purview labels are metadata, not enforcement).
6. **Monitor.** Use the Purview hub + audit logs to review label coverage and access.

## What Tier 0 does NOT do

- It does **not** transform or remove PHI — a sensitivity label is a *classification stamp*,
  not a redaction. Removing PHI is Tier 3.
- A catalog listing is **not** data access. Findability of names ≠ readability of data.

See [../docs/positioning_and_scope.md](../docs/positioning_and_scope.md) for the full
maturity ladder and where Tier 0 hands off to Tier 3.
