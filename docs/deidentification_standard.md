# PHI De-Identification Standard

> **Controlled document — template.** This is a fill-in-the-blanks policy template shipped
> with the accelerator. It is pre-mapped to the technical enforcement this repository already
> provides, so an auditor can trace each policy line to the exact Fabric / OneLake / Purview /
> code control that implements it. Replace every **[bracketed]** value with your organization's
> specifics and route through your governance process before it becomes authoritative.
>
> **SYNTHETIC DATA ONLY (as shipped).** Enforcement references describe the accelerator's
> reference pattern on synthetic data. Real PHI requires your own Safe Harbor / Expert
> Determination validation and a qualified reviewer — see
> [pre_real_phi_checklist.md](pre_real_phi_checklist.md).

| Field | Value |
|-------|-------|
| Document ID | `[ORG]-STD-DEID-001` |
| Version | `0.1 (template)` |
| Owner | `[Chief Privacy Officer]` |
| Classification | `Internal — Controlled` |
| Effective date | `[YYYY-MM-DD]` |
| Review cadence | Annual (or on material change to data flows, AI use, or regulation) |
| Supersedes | `[prior version / none]` |

---

## 1. Purpose Statement

`[Organization]` de-identifies Protected Health Information (PHI) before use in analytics, AI
model training, testing, research, and external sharing to reduce privacy risk and support
HIPAA compliance. Once the 18 HIPAA Safe Harbor identifiers are removed, generalized, or
tokenized, the data is no longer PHI and may flow to analytics, self-service reporting, and AI
without Business Associate Agreement (BAA) constraints.

| Control | Fabric / repo enforcement |
|---------|---------------------------|
| Purpose & rationale documented | [docs/positioning_and_scope.md](positioning_and_scope.md), [docs/hipaa_compliance.md](hipaa_compliance.md), [docs/architecture_and_rationale.md](architecture_and_rationale.md) |

---

## 2. Scope

| In scope | Out of scope |
|----------|--------------|
| AI / ML model training datasets | Direct patient-care (treatment) workflows |
| Analytics and reporting (Power BI, Copilot) | Clinical production systems of record |
| Research datasets | Medical-record retention obligations |
| Data shared with vendors / partners | Authorized treatment / payment / operations use of PHI |

This standard governs the **de-identification pipeline** (Bronze → Silver → **Silver-Deid** →
Gold) and the governed re-identification path. It does not alter lawful uses of PHI for
treatment, payment, or operations.

| Control | Fabric / repo enforcement |
|---------|---------------------------|
| Scope boundary ("not a certified service"; tiered maturity) | [docs/positioning_and_scope.md](positioning_and_scope.md) |
| Physical pipeline scope (where de-id happens) | [docs/security_model.md](security_model.md), notebook `02b_silver_deid` (single privileged crossing point) |

---

## 3. Regulatory Basis

| Requirement | Description | Enforcement in this repo |
|-------------|-------------|--------------------------|
| **HIPAA Safe Harbor** (45 CFR §164.514(b)(2)) | Remove/generalize the 18 identifiers | `safe_harbor` profile in [config/deid_rules.yaml](../config/deid_rules.yaml); mapping in [docs/safe_harbor_mapping.md](safe_harbor_mapping.md) |
| **HIPAA Expert Determination** (45 CFR §164.514(b)(1)) | Statistical assessment of re-identification risk | `expert_determination` profile (per-patient date-shift preserving intervals) + residual-risk metrics ([src/fabric_phi_deid/privacy_metrics.py](../src/fabric_phi_deid/privacy_metrics.py)) |
| **State privacy laws** | Additional requirements where applicable | `[document state-specific rules; extend deid_rules.yaml]` |
| **Internal governance** | Enterprise security & AI policies | [docs/enforcement_models.md](enforcement_models.md), [docs/hipaa_compliance.md](hipaa_compliance.md) |

AI use does **not** relax these requirements: a dataset used to train or prompt AI must meet
Safe Harbor or Expert Determination the same as any other secondary use.

---

## 4. Identifier Removal Matrix

The authoritative, **machine-executable** matrix is [config/deid_rules.yaml](../config/deid_rules.yaml).
The table below is the human-readable summary; the config is what actually runs.

| Identifier category | Action | Enforcement (strategy in config) |
|---------------------|--------|----------------------------------|
| Names (patient & provider) | Remove / replace | `synthesize` (irreversible fake data) |
| Social Security number | Remove | deny-by-default `suppress` + scorecard SSN pattern scan |
| Medical Record Number | Tokenize | `tokenize` (HMAC-SHA256, `PT-` prefix) |
| Account / beneficiary numbers | Tokenize | `tokenize` (where present) |
| Certificate / license / DEA / NPI | Tokenize | `tokenize` (`LIC-` / `DEA-` / `NP-`) |
| Email address | Remove | `suppress` + scorecard email pattern scan |
| Telephone / fax | Remove | `suppress` + scorecard phone pattern scan |
| Full address | Remove | `AddressLine1` → `suppress` |
| ZIP code | Truncate per policy | `generalize(zip3)`; `000` for low-population prefixes |
| Dates (DOB, service, encounter) | Generalize / shift | `generalize(year)` (Safe Harbor) or `date_shift` (Expert Determination); month suppressed |
| Ages > 89 | Aggregate | `generalize(age_cap=90)` |
| Device / biometric / images | Remove or redact | `suppress` (present-in-schema); free-text via `ner_text` |
| **Any unlisted column** | **Remove** | **Deny-by-default `suppress`** — new identifiers cannot leak by omission |

| Control | Fabric / repo enforcement |
|---------|---------------------------|
| Executable removal matrix | [config/deid_rules.yaml](../config/deid_rules.yaml) |
| 18-identifier → column → strategy mapping | [docs/safe_harbor_mapping.md](safe_harbor_mapping.md) |
| Deny-by-default (fail-closed) | `default_strategy: suppress` in config; validated by [src/fabric_phi_deid/config.py](../src/fabric_phi_deid/config.py) |

---

## 5. Tokenization Standard

Medical Record Number and other stable identifiers are replaced with **deterministic tokens**
(same input → same token, so joins survive) rather than deleted, enabling linkage across tables
without exposing the original value.

| Requirement | Standard | Enforcement in this repo |
|-------------|----------|--------------------------|
| Algorithm | HMAC-SHA256 with a secret pepper | [src/fabric_phi_deid/tokenization.py](../src/fabric_phi_deid/tokenization.py) |
| Token storage / crosswalk | Isolated **Vault** workspace, separate from AI/analytics | crosswalk `xwalk_*`; `NB_reidentify` runs only in Vault |
| Encryption / key management | Pepper stored in **Azure Key Vault**; never logged or written to a table | `get_pepper()`; rotation in [docs/pepper_rotation_runbook.md](pepper_rotation_runbook.md) |
| Authorized re-identification users | Least privilege; `[~2 named approvers]`; Security Administrator approval | Vault workspace membership; [docs/security_model.md](security_model.md) |
| Audit | Every re-identification request logged | audit logger in [src/fabric_phi_deid/audit.py](../src/fabric_phi_deid/audit.py); `NB_reidentify` is break-glass, audited |

> Medical Record Number is replaced with a deterministic token. Mapping tables are maintained
> within a secured Vault workspace isolated from AI workloads. Access requires
> `[Security Administrator]` approval and audit logging.

---

## 6. Re-Identification Risk Assessment

| Risk area | Evaluation | Enforcement in this repo |
|-----------|------------|--------------------------|
| Small population groups | Assessed | **k-anonymity** over quasi-identifiers (`BirthYear, Gender, Race, ZIP`) in `NB_scorecard` |
| Rare / skewed sensitive values | Assessed | **l-diversity** + **t-closeness** on the sensitive attribute |
| Combination (quasi-identifier) attacks | Assessed | [src/fabric_phi_deid/privacy_metrics.py](../src/fabric_phi_deid/privacy_metrics.py) (equivalence classes) |
| Cross-dataset linkage | Assessed | `[manual analyst review — document linkable datasets]` |
| External public datasets | Assessed | `[manual analyst review — document external sources considered]` |
| Free-text (clinical notes) identifiers | Detected + measured | [ner_text.py](../src/fabric_phi_deid/ner_text.py) (Presidio NER) + [eval_harness.py](../src/fabric_phi_deid/eval_harness.py) recall / F1 |

Quantitative metrics are computed automatically and persisted as evidence; **linkage against
external datasets is a documented manual review** performed by `[Privacy / Data Science]` before
release. Thresholds (default k≥5, l≥2, t≤0.2) are tunable in `NB_scorecard`.

---

## 7. AI-Specific Controls

The Gold layer that Power BI and Copilot read contains **no PHI by construction** — it is built
only from de-identified Silver, and the scorecard blocks publish if any identifier survives.

| Control | Requirement | Enforcement in this repo |
|---------|-------------|--------------------------|
| Prompt protection | PHI prohibited in public AI tools | AI reads only `gold_safe_*` (no PHI); tenant-boundary architecture |
| Model training | Only approved, de-identified datasets | Gold layer is the only AI-consumable; `[dataset approval record]` |
| Copilot usage | Approved tenant boundaries | 3-workspace isolation, [docs/security_model.md](security_model.md) |
| Agent access | Least-privilege | workspace RBAC; OneLake Security; [sql/rls_cls_policies.sql](../sql/rls_cls_policies.sql) |
| Vendor access | BAA required when processing PHI | `[organizational — track BAAs outside repo]` |
| Data retention | Defined retention schedule | `[organizational retention policy]` |

---

## 8. Audit Evidence Requirements

Retain the following as the litigation / OCR evidence trail:

| Evidence | Source (this repo) | Source (Fabric platform) |
|----------|--------------------|--------------------------|
| De-identification job logs | audit logger + per-run manifest ([audit.py](../src/fabric_phi_deid/audit.py)) | Spark/notebook run history |
| Config fingerprint (what rules ran) | `config_fingerprint()` in each manifest | — |
| Scorecard / risk assessment | PHI-free `scorecard_<id>.json` in `Files/audit/` | — |
| Detector quality (recall / F1) | [eval_harness.py](../src/fabric_phi_deid/eval_harness.py) against labeled fixture | — |
| Approval & determination records | determination method/reviewer/expiry in scorecard artifact | `[signed determination on file]` |
| Data lineage | — | Purview Data Map lineage |
| Sensitivity label assignments | Tier 0 classification → rulebook | Purview Information Protection |
| DLP policy history | — | Purview DLP |
| Access logs | re-identification audit entries | OneLake access logs, Fabric audit log exports |

> All persisted evidence is **metadata-only** (thresholds, checks, verdicts, fingerprints) — it
> contains **no data values**, so it is safe to retain long-term.

---

## 9. Roles and Responsibilities

| Role | Responsibility | Mapping in this repo |
|------|----------------|----------------------|
| Data Owner | Approves dataset use | `[named]` |
| Privacy Officer | Reviews compliance | `[named]` |
| Security Team | Validates controls | Vault workspace admins; `CODEOWNERS` |
| AI Governance Board | Approves AI use cases | `[named]` |
| Data Engineer | Executes transformation | Raw-workspace engineers (`~3`), notebook `02b` |
| Auditor | Reviews evidence | consumes `Files/audit/` artifacts |

Workspace-level separation of duties (Raw / Analytics / Vault) is defined in
[docs/security_model.md](security_model.md).

---

## 10. Sign-Off Page

This standard is reviewed and approved **annually** (and on material change). No real PHI is
processed until the pre-PHI gates in [docs/pre_real_phi_checklist.md](pre_real_phi_checklist.md)
are signed off.

| Role | Name | Approval | Date |
|------|------|----------|------|
| Chief Privacy Officer | `[    ]` | ☐ | `[    ]` |
| Security Officer | `[    ]` | ☐ | `[    ]` |
| Compliance Officer | `[    ]` | ☐ | `[    ]` |
| Data Governance Lead | `[    ]` | ☐ | `[    ]` |

---

## Appendix A — Litigation Defense Position

Evidence this standard + the accelerator can produce if challenged:

| # | Defensible claim | Evidence source |
|---|------------------|-----------------|
| 1 | PHI was identified and classified | Tier 0 classification → [config/deid_rules.yaml](../config/deid_rules.yaml) |
| 2 | A documented de-identification standard existed | **this document** + [docs/](.) |
| 3 | Tokenization/anonymization was applied consistently | deterministic [tokenization.py](../src/fabric_phi_deid/tokenization.py); scorecard proof |
| 4 | AI systems only accessed approved datasets | Gold = no PHI by construction; `gold_safe_*` only |
| 5 | BAAs existed with vendors | `[organizational records]` |
| 6 | Access was logged and monitored | audit logger; OneLake / Purview audit logs |
| 7 | Re-identification risk was assessed | k-anon / l-div / t-closeness + `scorecard_<id>.json` |
| 8 | Governance approvals were documented | §10 sign-off + determination metadata + [pre_real_phi_checklist.md](pre_real_phi_checklist.md) |

## Appendix B — Control → Fabric capability crosswalk (summary)

| Policy control | Fabric / Microsoft capability | Repo artifact |
|----------------|-------------------------------|---------------|
| Classification | Purview Information Protection (sensitivity labels) | Tier 0 runbook, `inventory_catalog.py` |
| Access enforcement | OneLake Security, workspace RBAC, RLS/CLS | [sql/rls_cls_policies.sql](../sql/rls_cls_policies.sql) |
| Transformation (de-id) | Fabric Spark notebooks | `02b_silver_deid`, `deid_engine.py` |
| Secret / key management | Azure Key Vault | `tokenization.py`, [pepper_rotation_runbook.md](pepper_rotation_runbook.md) |
| Risk assessment | (accelerator) | `privacy_metrics.py`, `NB_scorecard` |
| Evidence / audit | Fabric audit log, Purview lineage | `audit.py`, `Files/audit/scorecard_*.json` |
| Isolation of re-id | Separate workspace + break-glass | Vault workspace, `NB_reidentify` |

## Appendix C — Classification → rulebook crosswalk (Tier 0 → Tier 3)

**The bridge:** the sensitivity classification applied in the OneLake catalog / Purview (Tier 0)
becomes the input rulebook that drives de-identification (Tier 3). *The label you assign in the
catalog today becomes the rule that de-identifies the column tomorrow.* This makes the policy
self-consistent: an auditor can follow a single column from **label → rule → transformation →
evidence**.

| Purview / catalog classification | Example column(s) | Rulebook strategy ([deid_rules.yaml](../config/deid_rules.yaml)) | Proven by |
|----------------------------------|-------------------|------------------------------------------------------------------|-----------|
| Direct identifier — **Name** | `FirstName`, `LastName`, `PatientName` | `synthesize` (irreversible) | scorecard: no name patterns survive |
| Direct identifier — **MRN / stable ID** | `MRN`, `NPI`, `LicenseNumber`, `DEANumber` | `tokenize` (HMAC, prefixed) | scorecard: `PT-`/`NP-` prefix check |
| Direct identifier — **SSN / phone / email** | (pattern-detected) | deny-by-default `suppress` | scorecard: SSN/phone/email regex scan |
| Quasi-identifier — **Date** | `DateOfBirth`, `ServiceDate` | `generalize(year)` or `date_shift` | scorecard: no `DateOfBirth`; `BirthYear` only |
| Quasi-identifier — **Geography** | patient `ZIP` | `generalize(zip3)`; `000` low-pop | scorecard: ZIP ≤ 3 digits |
| Quasi-identifier — **Age** | `Age` | `generalize(age_cap=90)` | k-anonymity metric |
| Sensitive attribute | `Ethnicity`, diagnosis | passthrough + monitored | l-diversity / t-closeness metrics |
| **Unclassified / new column** | any | **deny-by-default `suppress`** | config coverage linter fails closed |

| Control | Fabric / repo enforcement |
|---------|---------------------------|
| Label taxonomy → rulebook mapping | [tier0/README.md](../tier0/README.md), [config/deid_rules.yaml](../config/deid_rules.yaml) |
| Inventory of classified columns | `tier0/inventory_catalog.py` (Catalog Search API) |
| Fail-closed on unclassified columns | `default_strategy: suppress` + [config.py](../src/fabric_phi_deid/config.py) coverage linter |

