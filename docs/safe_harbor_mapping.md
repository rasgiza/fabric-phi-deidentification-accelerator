# Safe Harbor Identifier Mapping

> **SYNTHETIC DATA ONLY** (accelerator). Mapping is illustrative for the two bundled
> synthetic source schemas. Validate completeness against your own schema with a qualified
> reviewer.

HIPAA **Safe Harbor** (45 CFR §164.514(b)(2)) requires removing or generalizing **18**
identifier types. Below: each identifier, where it appears in each bundled source, and the
strategy the engine applies (see [`config/deid_rules.yaml`](../config/deid_rules.yaml)).

> ### The shipped profile cannot claim Safe Harbor — and that is deliberate
>
> The `safe_harbor` profile name describes its **column treatments** (dates → year, ZIP → 3
> digits, ages capped at 90). It is not a certification that the output qualifies for a Safe
> Harbor claim, because the `tokenize` rules in rows 8, 11 and 18 emit values **derived from**
> the individual. §164.514(c)(1) admits a re-identification code only when it is "not derived
> from or related to information about the individual", and HHS §2.9 permits hash-derived
> values under **Expert Determination**, with the keys undisclosed.
>
> Those tokens are what let the de-identified Caboodle and Clarity stars still join — the whole
> point of the accelerator — so the trade is intentional. The cost is that the claimable method
> is Expert Determination. `determination.assess_method_eligibility()` computes this from the
> rules themselves and `NB_scorecard` **hard-fails** a mismatched claim, so the constant cannot
> quietly drift into a false one.
>
> ### If you need the claim, use `safe_harbor_strict`
>
> That profile is what "remove every `tokenize` / `date_shift` rule" actually looks like, paid
> for at the stated price. It carries **zero** derived values, so `assess_method_eligibility()`
> returns `safe_harbor` and the claim is mechanically verified rather than asserted.
>
> It **covers both source systems.** For a long time it did not, and the reason was read as a
> law of nature: linking the two stars needs a stable per-patient key, the only shared value is
> the MRN, and §164.514(c)(1) admits a re-identification code only when it is "not derived from
> or related to information about the individual" — so an HMAC of the MRN is out. That is all
> true, and it rules out a **derived** key. It does not rule out an **assigned** one.
>
> So under `safe_harbor_strict` the MRN becomes a `surrogate`: a random `DEID-…` value minted
> once, held in a crosswalk in **PHI-Vault**, and reused for that patient everywhere. It is not
> derived from anything about the individual, which is precisely what §164.514(c) contemplates.
> Caboodle and Clarity land on the same code and the conformed dimension works.
>
> **The price is custody, and it is a real one.** A token is recomputable from the pepper
> forever; a surrogate exists only in that crosswalk table, so losing it loses every linkage,
> and anyone who obtains it holds the map back to identity. That is why the crosswalk lives in
> a separate workspace with its own access control, and why Expert Determination deliberately
> keeps `tokenize` rather than "upgrading" to surrogates. Neither is strictly better;
> `tests/test_profile_reaches_the_data.py` pins both so the distinction cannot be tidied away.
>
> | | `safe_harbor_strict` | `safe_harbor` / `expert_determination` |
> |---|---|---|
> | MRN | `DEID-…` **assigned** code (crosswalk in Vault) | `PT-…` **derived** HMAC token |
> | Patient / provider names | removed (NULL) | synthesized |
> | Cross-source patient match | available | available |
> | Recover linkage after losing the mapping | **impossible** — the map *is* the linkage | recompute from the pepper |
> | Free-text notes | suppressed | NER-redacted |
> | Provider NPI / license / DEA | suppressed | tokenized |
> | Qualified expert required | **no** | yes |
> | Re-certification | n/a | practitioners commonly time-limit |
>
> Names are **suppressed rather than synthesized**, which is the least obvious line in the
> table. `strat_synthesize` HMACs the source value and indexes a name list with the digest, so
> the same real name always yields the same fake one — a consistent, pepper-keyed pseudonym
> derived from the individual. Only 256 combinations exist, so it re-identifies almost nobody,
> but HHS §3.2 rules out even patient *initials*: "derived but lossy" is not a category the rule
> recognises. `synthesize` is therefore counted as a derived value by
> `assess_method_eligibility()`.
>
> Surrogate keys (`PatientKey`, `EncounterKey`) survive in both. They are warehouse-minted
> integers, not derived from or related to the individual, so they fall under the §164.514(c)
> code exception — which is what keeps the star schema joinable *within* Caboodle.


The two sources are deliberately different shapes — **Caboodle** (dimensional warehouse)
and **Clarity** (normalized transactional). The same engine handles both; only the column
names differ, which is exactly the point.

| # | Safe Harbor identifier | Caboodle column(s) | Clarity column(s) | Strategy |
|---|------------------------|--------------------|-------------------|----------|
| 1 | Names | `FirstName`, `LastName`, `PatientName`, provider names | `PAT_NAME`, `PAT_FIRST_NAME`, `PAT_LAST_NAME`, `PROV_NAME` | `synthesize` |
| 2 | Geographic subdivisions < state | patient `ZIP` | `ZIP`, `CITY`, `ADD_LINE_1` | `generalize(zip3)` (`000` for low-pop); city/street `suppress` |
| 3 | Dates (except year) related to an individual | `DateOfBirth`, `ServiceDate`, `EncounterDate`, `ScoreDate`, `*Month` | `BIRTH_DATE`, `DEATH_DATE`, `PAT_ENC_DATE`, `CONTACT_DATE`, `HOSP_ADMSN_TIME`, `HOSP_DISCH_TIME`, `ORDERING_DATE`, `START_DATE`, `END_DATE`, `PROC_START_TIME`, `RESULT_DATE`, `ENC_MONTH` | `generalize(year)`; **birth dates use `generalize(birth_year)`** (see row — below) / `date_shift` by `PAT_ID` (Expert Determination); month suppressed |
| 4 | Telephone numbers | `HomePhone`, `MobilePhone`, `GuarantorPhone` | `HOME_PHONE`, `WORK_PHONE`, `MOBILE_PHONE`, `GUAR_HOME_PHONE` | `suppress` |
| 5 | Fax numbers | `GuarantorFaxNumber` | `GUAR_FAX` | `suppress` |
| 6 | Email addresses | `Email` | `EMAIL_ADDRESS` | `suppress` |
| 7 | Social Security numbers | `SSN` | `SSN` | `suppress` (scorecard also scans for SSN patterns) |
| 8 | Medical record numbers | `MRN` | `PAT_MRN_ID` | `tokenize` (HMAC, `PT-`) — **shared namespace**, so the same patient yields the same token in both schemas. Under `safe_harbor_strict`: `surrogate` (`DEID-`) |
| 9 | Health plan beneficiary numbers | `HealthPlanMemberID` | `SUBSCRIBER_ID`, `GROUP_NUM` | `suppress` |
| 10 | Account numbers | `HospitalAccountNumber`, `GuarantorAccountNumber` | `ACCT_BILLING_NUM` | `suppress` (the internal keys `HospitalAccountKey` / `HSP_ACCOUNT_ID` pass through — see the CSN note below) |
| 11 | Certificate / license numbers | `DriversLicenseNumber`, `LicenseNumber`, `DEANumber` | — | `suppress` (patient), `tokenize` (provider) |
| 12 | Vehicle identifiers | `VehicleIdentificationNumber`, `LicensePlateNumber` | — | `suppress` |
| 13 | Device identifiers / serial numbers | `SerialNumber`, `UDI` | — | `suppress`; `ModelNumber` passes through (a model is a product, not a person) |
| 14 | Web URLs | `AccessedURL` | — | `suppress` |
| 15 | IP addresses | `SourceIPAddress` | — | `suppress` |
| 16 | Biometric identifiers | `BiometricTemplateID` | — | `suppress` |
| 17 | Full-face photos / comparable images | `FacePhotoURI` | — | `suppress` |
| 18 | Any other unique identifying number/characteristic/code | `NPI` | `NPI`, `PAT_ID`, `PAT_ENC_CSN_ID` | `tokenize` (`NP-` / `EP-` / `ENC-`) — derived value; see the note above |
| — | Ages > 89 must be aggregated, **and so must dates indicative of such age** | `Age`, `DateOfBirth` | `AGE`, `BIRTH_DATE` | `generalize(age_cap=90)` **and** `generalize(birth_year, cap_age=90)` |

## Notes on judgment calls

- **Capping `Age` at 90 is not enough on its own.** §164.514(b)(2)(i)(C) removes ages over 89
  *and* "all elements of dates (including year) indicative of such age". Publishing a capped
  `Age = 90` next to a true `BirthYear` defeats the cap — the year reconstructs the age that
  was just removed. The `birth_year` kind therefore floors every birth year old enough to imply
  90+ into a single bucket (`reference_year - cap_age`), matching HHS's worked example: born
  1910, seen in 2010, report "on or before 1920". Ordinary service and encounter dates keep the
  plain `year` kind — they are not indicative of age.

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

- **The guarantor is not the patient, and the rule reaches them anyway.**
  §164.514(b)(2)(i) removes identifiers "of the individual **or of relatives, employers, or
  household members** of the individual" — the clause most implementations miss. A guarantor
  name, phone or account number on `dim_hospital_account` is almost always a relative, so it is
  suppressed. `GuarantorRelationship` ("Spouse", "Parent") passes through: it describes a
  category shared by millions, not a person.

- **Removing the 18 identifiers is only half of Safe Harbor.** §164.514(b)(2) is two conditions
  joined by AND: (i) remove the identifiers, and (ii) the covered entity has no **actual
  knowledge** that the residual information could identify someone. No scan can establish (ii)
  — it is a statement about what you know about your own estate. The rulebook therefore carries
  an `actual_knowledge:` block that ships **unsigned**, and `NB_scorecard` fails any Safe Harbor
  claim until a named individual fills it in and dates it. Deleting eighteen columns is the part
  that automates; knowing your own data is the part that does not.

The `NB_scorecard` notebook asserts these outcomes over `gold_safe_*` before publish, and
verifies suppression directly against `silver_deid_*` — where a suppressed column still exists
and is NULL. Scanning gold alone would be vacuous: gold only projects the columns the star
model asked for, so a column still full of SSNs that gold never selected produces exactly the
same clean result as one properly emptied.

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

