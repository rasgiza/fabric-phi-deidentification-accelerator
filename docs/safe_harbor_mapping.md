# Safe Harbor Identifier Mapping

> **SYNTHETIC DATA ONLY** (accelerator). Mapping is illustrative for the Caboodle provider
> dataset. Validate completeness against your own schema with a qualified reviewer.

HIPAA **Safe Harbor** (45 CFR §164.514(b)(2)) requires removing or generalizing **18**
identifier types. Below: each identifier, whether it appears in this dataset, and the
strategy the engine applies (see [`config/deid_rules.yaml`](../config/deid_rules.yaml)).

| # | Safe Harbor identifier | In this dataset? | Column(s) | Strategy |
|---|------------------------|------------------|-----------|----------|
| 1 | Names | Yes | `FirstName`, `LastName`, `PatientName`, provider names | `synthesize` |
| 2 | Geographic subdivisions < state | Yes | patient `ZIP` | `generalize(zip3)`; `000` for low-pop |
| 3 | Dates (except year) related to an individual | Yes | `DateOfBirth`, `ServiceDate`, `EncounterDate`, `ScoreDate`, `*Month` | `generalize(year)` / `date_shift`; month suppressed |
| 4 | Telephone numbers | No | — | (scorecard scans for phone patterns) |
| 5 | Fax numbers | No | — | — |
| 6 | Email addresses | No | — | (scorecard scans for email patterns) |
| 7 | Social Security numbers | No | — | (scorecard scans for SSN patterns) |
| 8 | Medical record numbers | Yes | `MRN` | `tokenize` (HMAC, `PT-`) |
| 9 | Health plan beneficiary numbers | No (payer is org-level) | — | — |
| 10 | Account numbers | No | — | — |
| 11 | Certificate / license numbers | Yes | `LicenseNumber`, `DEANumber` | `tokenize` |
| 12 | Vehicle identifiers | No | — | — |
| 13 | Device identifiers / serial numbers | No | — | — |
| 14 | Web URLs | No | — | — |
| 15 | IP addresses | No | — | — |
| 16 | Biometric identifiers | No | — | — |
| 17 | Full-face photos / comparable images | No | — | — |
| 18 | Any other unique identifying number/characteristic/code | Provider `NPI` | `NPI` | `tokenize` (optional; on by default) |
| — | Ages > 89 must be aggregated | Yes | `Age` | `generalize(age_cap=90)` |

## Notes on judgment calls

- **Facility ZIP is kept.** Identifier #2 concerns the *individual's* geography. The facility
  (covered-entity) address ZIP in `dim_facility` is organizational, not patient geography, so
  it is retained; the facility **street address** (`AddressLine1`) is suppressed.
- **Provider identifiers (NPI/license/DEA)** are not patient PHI, but they identify an
  individual provider. The engine tokenizes them by default (disable in config if your use
  case needs real provider IDs).
- **Record-management dates** (`EffectiveDate`, `ExpirationDate` on SCD dimensions) describe
  the *record*, not a care event for the individual, so they pass through. Revisit for your
  data model if these encode patient events.
- **Deny-by-default.** Any column not listed in the rulebook is **suppressed**, so a newly
  added identifier can't leak by omission. The scorecard additionally scans for SSN/phone/
  email patterns as a backstop.

The `NB_scorecard` notebook asserts these outcomes over `gold_safe_*` before publish.

## Strategy glossary: which treatment each field gets and why

The engine applies one of a small set of strategies to every column. They are **not**
interchangeable — the choice depends on whether the field has analytic value and whether you
ever need to link on it or recover it. This is the source of the common confusion that
"replacing a name with a fake name is tokenization" — **it is not** (see below).

| Strategy | What it does | Reversible? | In the Vault crosswalk? | Chosen when… | Example fields |
|----------|--------------|-------------|-------------------------|--------------|----------------|
| **tokenize** | Replaces the value with a **deterministic** HMAC token (same input → same token), e.g. `PT-3f9a…`. Keeps records joinable without exposing the value. | **Yes** — via governed re-identification in the Vault | **Yes** | You must **link records** across tables or occasionally **re-identify** under approval | `MRN`→`PT-`, `NPI`→`NP-`, `LicenseNumber`→`LIC-`, `DEANumber`→`DEA-` |
| **synthesize** | Replaces the value with **realistic fake data** (a made-up name). The fake value is **not** derived from the original and is stored nowhere. | **No** — the original is discarded | No | The field is a direct identifier with **no analytic value** and no linkage need | `FirstName`, `LastName`, `PatientName`, provider names |
| **generalize** | Reduces precision so the detail can't identify: date → year, ZIP → 3-digit (`000` low-pop), age → capped at 90. | No | No | The field has value at a **coarser grain** | `DateOfBirth`→year, `ZIP`→zip3, `Age`→cap 90, `ServiceDate`→year |
| **date_shift** | *(Expert Determination profile)* Shifts each patient's dates by a consistent **per-patient** offset, so **intervals are preserved** but absolute dates are not. | No (offset kept in Vault) | Offset only | You need date **intervals** (length of stay, time-to-event) under Expert Determination | `DateOfBirth`, `ServiceDate` (ED profile) |
| **suppress** | Drops the value entirely. **This is the default** for any unlisted column (deny-by-default). | No | No | Identifier with no needed value, or an unclassified column | `AddressLine1`, `ServiceMonth`, any unlisted column |
| **passthrough** | Keeps the value unchanged. Only for **non-identifying** columns. | n/a | n/a | Surrogate key, measure, or already-generalized attribute | `PatientKey`, `Gender`, `Race`, `BilledAmount` |
| **redact_text** | *(Free text)* Detects identifiers **inside** a text field and labels / tokenizes / removes only those spans. | Depends on mode | If tokenized | The column is free text (notes/comments) that may embed PHI | clinical notes via `ner_text` |

### The distinction you asked about: fake names are `synthesize`, not `tokenize`

- **Fake name = `synthesize`.** A synthesized name is **random fake data with no link back** to
  the real person — **irreversible**, and nothing is stored that could reverse it. You would
  never want to (or be able to) recover the original name, so throwing it away and dropping in a
  plausible fake is the safest choice.
- **`tokenize` is the opposite intent.** It's a **deterministic, governed-reversible** stand-in
  for identifiers you must be able to **join on** (the same `MRN` must produce the same `PT-…`
  token in every table) or **re-identify** later through the isolated Vault crosswalk (e.g. a
  legitimate treatment need, always approved + audited).

**Why the split?** Names carry no analytic value and never need recovery → `synthesize`
(discard). `MRN` / `NPI` are the **linkage backbone** and may need governed re-identification →
`tokenize` (deterministic, mapping locked in the Vault). Dates and ZIP retain analytic value at
a coarser grain → `generalize` (or `date_shift` when intervals matter).

> **Rule of thumb:** *need to link or recover it?* → `tokenize`. *Direct identifier you never
> need again?* → `synthesize` (names) or `suppress` (addresses). *Useful but too precise?* →
> `generalize` / `date_shift`.

