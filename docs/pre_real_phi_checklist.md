# Pre-Real-PHI Checklist

> **This accelerator ships SYNTHETIC-DATA-ONLY.** Passing the automated gates below is
> **necessary but not sufficient** to run against real PHI. HIPAA de-identification is a
> determination made by people and process — not something code can certify on its own.

Do **not** point this pipeline at real PHI until every box is checked and signed off.

## A. Legal / compliance gate (people, not code)

- [ ] A **HIPAA de-identification method is chosen and documented**: Safe Harbor
      (§164.514(b)(2)) *or* Expert Determination (§164.514(b)(1)).
- [ ] If **Expert Determination**: a qualified statistician/expert has reviewed the
      `expert_determination` profile, the residual re-identification risk, and signed a
      written determination. Retain it.
- [ ] The 18 Safe Harbor identifiers are mapped to real source columns and reviewed against
      `docs/safe_harbor_mapping.md` — including **free-text** fields (notes/comments) that
      may embed identifiers. (This accelerator de-identifies *structured* columns; free-text
      NER is a separate, additional control — see the roadmap `src/ner_text.py`.)
- [ ] A **Business Associate Agreement (BAA)** and data-use terms cover every workspace and
      downstream consumer of the de-identified output.
- [ ] Data classification / catalog labels (Tier 0) are complete and are the authoritative
      source of the rulebook.

## B. Configuration gate (code-assisted)

- [ ] `validate_config(cfg)` returns **no errors** for the active profile.
- [ ] `audit_coverage(cfg, profile, table, real_columns)` reports **zero `defaulted`**
      columns you did not intend to suppress, and **zero `missing`** (typo/schema-drift)
      rules — for **every** table, against the **real** schema.
- [ ] Every kept (`passthrough`) column is a deliberate, reviewed decision (no measure or
      key silently carries an identifier).
- [ ] `active_profile` is set to the intended profile.

## C. Secret / access gate

- [ ] Pepper is a fresh high-entropy secret in Key Vault; `PHI_DEID_KEYVAULT_URL` set via
      environment; `get_pepper()` succeeds and the min-length check passes.
- [ ] Key Vault RBAC is least-privilege (de-id identity = Secrets User, read-only).
- [ ] The **three-workspace isolation** is enforced (Raw / Analytics / Vault) with correct
      role assignments; crosswalk + `NB_reidentify` are **Vault-only**.
- [ ] RLS/CLS policies (`sql/rls_cls_policies.sql`) are applied as defense-in-depth.

## D. Validation gate (code)

- [ ] Full test suite green, **including** the Spark integration tests (`pytest`, not just
      `-m "not spark"`).
- [ ] `NB_scorecard` runs on the **real** `gold_safe_*` output and **passes all hard
      asserts** (0 of the 18 identifiers detectable; MRN prefix present; no `DateOfBirth`;
      ZIP ≤ 3 digits; residual-PHI regex scan clean).
- [ ] k-anonymity on the chosen quasi-identifier set meets your policy threshold (advisory in
      the synthetic demo; make it a **hard gate** for real data).
- [ ] `bandit`, `pip-audit`, and `gitleaks` are green in CI.

## E. Operational gate

- [ ] A **run manifest** (`audit.build_run_manifest`) is emitted and retained per run
      (profile, config SHA-256, pepper key version, per-table counts) — PHI-free.
- [ ] Audit logging is enabled and verified to contain **no data values**.
- [ ] Pepper-rotation and incident-response runbooks are in place
      (`docs/pepper_rotation_runbook.md`).
- [ ] A rollback/quarantine plan exists for a suspected leak (freeze, purge, rotate, re-run).

## Sign-off

| Role | Name | Date | Signature |
| --- | --- | --- | --- |
| Data Protection / Privacy Officer | | | |
| Security (workspace + Key Vault) | | | |
| Expert Determination statistician (if applicable) | | | |
| Data Platform / Engineering owner | | | |

Only when this table is fully signed should the SYNTHETIC-ONLY guardrail be lifted for a
specific, scoped dataset.
