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
