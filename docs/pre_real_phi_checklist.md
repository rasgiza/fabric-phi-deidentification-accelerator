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
      may embed identifiers. Free text is handled by the `redact_text` strategy
      (`src/fabric_phi_deid/ner_text.py`); confirm every narrative column in your schema has a
      rule, not just the ones the sample data happens to exercise.
- [ ] The **Presidio backend is installed** (`pip install 'fabric-phi-deid[nlp]'`) in the Spark
      environment. Without it `ner_text` falls back to a regex detector that catches structured
      identifiers (MRN, phone, email, SSN, card, IP, URL) but **not** names, places, or dates.
      The scorecard hard-gates the structured subset but reports recall as `NOT_EVALUATED` —
      it does **not** publish a recall figure. **Benchmark the detector on your own annotated
      notes before trusting it on real narrative text.**
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
- [ ] The committed `DEMO_PEPPER` cell has been **deleted** from `02b_silver_deid` and
      `NB_reidentify`, and `PHI_DEID_ALLOW_COMPROMISED_PEPPER` is **not set anywhere** — not in
      the notebooks, not in the environment, not in a Spark pool configuration. That variable
      exists only to let the synthetic demo run; with real data its presence means tokens are
      keyed on a value published on the internet. Confirm `pepper_key_version` in the run
      manifest does **not** end in `-PUBLISHED-COMPROMISED`.
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
- [ ] k-anonymity on the chosen quasi-identifier set meets your policy threshold **with no
      `accepted_risk` waiver in `config/deid_rules.yaml`**. The gate is hard, but it is
      waivable, and the shipped demo ships waived (`accepted_by: "UNSIGNED"`). A run that
      reports `PASSED_WITH_ACCEPTED_RISK` has **not** met the threshold — it has recorded that
      someone accepted missing it. Before real PHI: generalize further, suppress the residual
      tail (`suppress_quasi_identifiers_spark`), or have a named, dated, in-scope signer.
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
