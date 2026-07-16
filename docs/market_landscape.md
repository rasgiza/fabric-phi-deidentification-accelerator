# Market Landscape: Build vs. Buy for PHI De-Identification

> **SYNTHETIC DATA ONLY** (accelerator). Vendor facts change — verify current offers/terms.

## Three categories of tooling

| Category | What it does | Examples | Where this accelerator fits |
|----------|--------------|----------|------------------------------|
| **De-identification / masking** | Remove or transform identifiers in existing data | This accelerator; Immuta; Privitar; cloud-native masking | **Build** option, in-tenant, structured data |
| **Synthetic data generation** | Create realistic fake data (from schema or seeded by real) | **Tonic.ai**; Gretel; MDClone | Complementary "buy"; used to make the demo data |
| **PII/PHI detection (NER)** | Find identifiers in free text | Microsoft **Presidio** (OSS); Azure AI Language PII; Tonic Textual | Pluggable into the engine for notes/text fields |

## Buy vs. build in the Microsoft ecosystem

| | **Build (this pattern)** | **Buy (e.g. Tonic, Immuta)** |
|---|---|---|
| Control / customization | Maximum — your code, your rules | Vendor-bounded |
| Data residency | In-tenant, nothing leaves Fabric | Depends on vendor architecture |
| Cost model | Compute only; no per-record fees | Licensing / per-record / seats |
| Certified determinations | You own the validation | Often provided by vendor |
| Free-text NER at scale | Presidio stub → build out | Mature, out-of-the-box |
| Support / warranty | Self-supported | Vendor SLA |
| Time-to-value | Fast for structured Safe Harbor rules | Fast for complex/text-heavy needs |

## On Tonic specifically

- Tonic.ai is a **live Azure Marketplace offer** ("Synthetic data solutions for software and
  AI development") and is **Azure-benefit-eligible = MACC-eligible** — it can be procured
  against existing Azure commitments.
- The customer **already generated their synthetic Caboodle data with Tonic**, so Tonic and
  this accelerator are **complementary**: Tonic makes safe test data; this accelerator
  de-identifies *production* pipelines in-tenant. Tonic **Textual** is the natural upgrade
  for the free-text NER slot (currently a Presidio stub in `src/ner_text` future work).

## Recommendation framing for the customer

- **Structured data + clear Safe Harbor rules + want in-tenant control** → **build** with this
  accelerator.
- **Heavy free-text, need certified determinations / vendor warranties, or synthetic test
  data** → **buy** (Tonic/Immuta), and let this accelerator handle the structured medallion.
- It is **not** either/or: the maturity ladder (Tier 0–3) and a buy component can coexist.
