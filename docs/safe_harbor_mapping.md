# Safe Harbor Identifier Mapping

> **SYNTHETIC DATA ONLY** (accelerator). Mapping is illustrative for the two bundled
> synthetic source schemas. Validate completeness against your own schema with a qualified
> reviewer.

HIPAA **Safe Harbor** (45 CFR §164.514(b)(2)) requires removing or generalizing **18**
identifier types. Below: each identifier, where it appears in each bundled source, and the
strategy the engine applies (see [`config/deid_rules.yaml`](../config/deid_rules.yaml)).

The two sources are deliberately different shapes — **Caboodle** (dimensional warehouse)
and **Clarity** (normalized transactional). The same engine handles both; only the column
names differ, which is exactly the point.

| # | Safe Harbor identifier | Caboodle column(s) | Clarity column(s) | Strategy |
|---|------------------------|--------------------|-------------------|----------|
| 1 | Names | `FirstName`, `LastName`, `PatientName`, provider names | `PAT_NAME`, `PAT_FIRST_NAME`, `PAT_LAST_NAME`, `PROV_NAME` | `synthesize` |
| 2 | Geographic subdivisions < state | patient `ZIP` | `ZIP`, `CITY`, `ADD_LINE_1` | `generalize(zip3)` (`000` for low-pop); city/street `suppress` |
| 3 | Dates (except year) related to an individual | `DateOfBirth`, `ServiceDate`, `EncounterDate`, `ScoreDate`, `*Month` | `BIRTH_DATE`, `DEATH_DATE`, `PAT_ENC_DATE`, `CONTACT_DATE`, `HOSP_ADMSN_TIME`, `HOSP_DISCH_TIME`, `ORDERING_DATE`, `START_DATE`, `END_DATE`, `PROC_START_TIME`, `RESULT_DATE`, `ENC_MONTH` | `generalize(year)` (Safe Harbor) / `date_shift` by `PAT_ID` (Expert Determination); month suppressed |
| 4 | Telephone numbers | — | `HOME_PHONE` | `suppress` |
| 5 | Fax numbers | — | — | — |
| 6 | Email addresses | — | `EMAIL_ADDRESS` | `suppress` |
| 7 | Social Security numbers | — | — | (scorecard scans for SSN patterns) |
| 8 | Medical record numbers | `MRN` | `PAT_MRN_ID` | `tokenize` (HMAC, `PT-`) — **shared namespace**, so the same patient yields the same token in both schemas |
| 9 | Health plan beneficiary numbers | (payer is org-level) | — | — |
| 10 | Account numbers | — | — | — |
| 11 | Certificate / license numbers | `LicenseNumber`, `DEANumber` | — | `tokenize` |
| 12 | Vehicle identifiers | — | — | — |
| 13 | Device identifiers / serial numbers | — | — | — |
| 14 | Web URLs | — | — | — |
| 15 | IP addresses | — | — | — |
| 16 | Biometric identifiers | — | — | — |
| 17 | Full-face photos / comparable images | — | — | — |
| 18 | Any other unique identifying number/characteristic/code | `NPI` | `NPI`, `PAT_ID`, `PAT_ENC_CSN_ID` | `tokenize` (`NP-` / `EP-` / `ENC-`) |
| — | Ages > 89 must be aggregated | `Age` | `AGE` | `generalize(age_cap=90)` |

## Notes on judgment calls

- **`PAT_ENC_CSN_ID` is tokenized; Caboodle's `EncounterKey` is not.** Both are "the
  encounter identifier," but a CSN is printed on discharge paperwork and displayed in the
  Epic UI, which makes it a real-world identifier under #18. A warehouse surrogate key that
  never leaves the database is not. Identical concept, different disclosure risk — this is
  the kind of call a generic scanning tool gets wrong in both directions.
- **`PAT_ID` is tokenized even though it looks like a surrogate.** In Clarity it is *the*
  patient key across seven tables and is exposed in downstream Epic tooling, so it is
  treated as an identifier rather than an internal artifact.
- **`STATE_C_NAME` is passed through.** Safe Harbor removes geography *smaller than* a
  state; the state itself is permitted.
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

