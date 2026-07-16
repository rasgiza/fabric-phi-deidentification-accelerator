# Positioning & Scope

> **SYNTHETIC DATA ONLY.** This accelerator is a **reference / blueprint pattern**, not a
> product and **not a certified de-identification service.**

## PG positioning guidance (lead with this for new customers)

> **For new customers, position Microsoft Fabric as Microsoft's primary data governance
> solution, with the OneLake catalog as its unified governance foundation.** The OneLake
> catalog gives customers one place to **discover, manage, and govern** data across
> **multi-cloud and hybrid** environments — so governance is a native capability of the
> data platform, not a bolt-on.

This accelerator sits **on top of** that foundation: Tier 0 is exactly the OneLake-catalog
governance starting point above, and Tier 3 (de-identification) is the capstone it unlocks.
When opening with a new customer, lead with Fabric + OneLake catalog as the governance
platform, then use the ladder below to show where PHI de-id fits.

## The maturity ladder

De-identification is the top of a ladder. Selling/building Tier 3 without Tier 0–2 in
place is why customers stall. This accelerator packages **Tier 0** (fundamentals) and
**Tier 3** (de-id) under one umbrella so the customer can start where they actually are.

| Tier | Name | Question it answers | Fabric building blocks | Status |
|------|------|---------------------|------------------------|--------|
| **Tier 0** | Catalog enablement & classification | *Can people find & trust our data?* | OneLake Catalog, Purview labels, domains | Available now (mostly UI) |
| **Tier 1** | Access governance | *Does the right person see the right bytes?* | OneLake security, RLS/CLS, workspace roles | Available now |
| **Tier 2** | Monitoring & audit | *Can security prove it?* | Purview hub, audit/activity logs | Available now |
| **Tier 3** | **PHI de-identification & tokenization** | *Can we use this data for analytics/AI without it being PHI?* | **This engine** (Spark + Key Vault) | Reference pattern (this repo) |

**How to present it:** lead with Tier 0 (the customer's real first need), show Tier 3 as the
**~15% north-star capstone** it unlocks. Tier 3 is the reason to invest in Tiers 0–2.

## What this accelerator IS

- A working, config-driven **pattern** for tokenization + Safe Harbor / Expert Determination
  de-identification on a Fabric medallion, demonstrated on **synthetic** Caboodle data.
- Built from **Microsoft-native** parts (Spark, Azure Key Vault, OneLake security). No data
  leaves the tenant.
- A **teaching tool + starting codebase** your engineers can adapt.

## What this accelerator is NOT

- **Not a certified de-identification service.** It does not, by itself, make data "HIPAA
  de-identified." Safe Harbor requires removing/validating all 18 identifiers for *your*
  data; Expert Determination requires a qualified statistician's sign-off.
- **Not legal or compliance advice**, and **not** a HIPAA certification. See
  [hipaa_compliance.md](hipaa_compliance.md).
- **Not a replacement** for a commercial de-id/synthetic-data product where you need vendor
  warranties, certified determinations, or advanced free-text NER at scale (see
  [market_landscape.md](market_landscape.md)).

## Buy vs. build in the Microsoft ecosystem

- **Build (this pattern):** maximum control, in-tenant, no per-record licensing, fits an
  existing Fabric medallion. Best for structured data + clear Safe Harbor rules.
- **Buy (e.g. Tonic, Immuta):** certified determinations, mature free-text handling,
  vendor support. Tonic is on the **Azure Marketplace** (Azure-benefit / **MACC-eligible**),
  so it can be procured through existing Azure commitments. The customer already generated
  their synthetic Caboodle data **with Tonic** — so "buy" and "build" are complementary,
  not either/or.

## Compliance boundary (read before any real-data use)

1. Validate the rulebook against your actual schema with a qualified reviewer.
2. Confirm all 18 Safe Harbor identifiers are addressed (or obtain Expert Determination).
3. Sign/verify the Microsoft **BAA** and complete a security review.
4. Treat the scorecard as *evidence the pattern ran*, not as a certification.
