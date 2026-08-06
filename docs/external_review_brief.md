# External review brief

This accelerator makes compliance claims. Every one of them was written, tested, and verified by
the same author, which is precisely the arrangement that lets a wrong assumption survive review.
The purpose of this document is to make an external review cheap enough to actually happen: it
tells a reviewer where the load-bearing assumptions are, so they do not have to read 5,000 lines
to find them.

**What is being asked:** one reviewer who did not write this code, spending a focused hour on the
questions in §2. Not a full audit. The questions are ordered by how much damage a wrong answer
does.

**Who is useful:** someone who has mapped a real Epic Clarity or Caboodle schema for a research or
analytics extract — an EHR data architect, a health-system privacy officer, or a de-identification
practitioner. Familiarity with Fabric is *not* required; almost nothing here is Fabric-specific.

---

## 1. What has already been verified, so you can skip it

Please do not spend your hour re-checking these. They are covered by 341 automated tests and by
end-to-end runs against a live 50,200-patient synthetic estate:

- The transforms do what the rulebook says (tokenization is deterministic and reversible only with
  the pepper; date shifting is per-patient consistent; ZIP truncation and birth-year flooring
  behave as specified).
- The scorecard's assertions actually fire. Each gate has been observed failing on purpose, not
  just passing — including the privacy gates, which were negative-tested by expiring their waivers
  in the live config and confirming the run fails.
- No PHI reaches the Gold layer in the shipped pipeline, per the residual-identifier scan.
- Reported metrics match independent probes of the underlying tables.

## 2. The questions worth your hour

### Q1. Is `config/deid_rules.yaml` right about a *real* Clarity schema?

**This is the highest-value question and the one we are least able to answer ourselves.** The
rulebook covers 8 Clarity tables — `clarity_patient`, `clarity_pat_enc`, `clarity_pat_enc_hsp`,
`clarity_pat_enc_dx`, `clarity_order_med`, `clarity_order_proc`, `clarity_order_results`,
`clarity_ser` — but it was written against a *synthetic* Clarity-shaped dataset, not a production
extract.

Specifically:

- **Which identifier-bearing columns are missing entirely?** A column absent from the rulebook is
  a column that passes through untouched. Free-text and comment columns are the likeliest gap
  (`*_COMMENT`, `*_NOTE`, `NARRATIVE`, `REASON_*`), as are secondary identifiers such as
  guarantor, employer, next-of-kin, and address-detail columns.
- **Are any columns mis-typed?** A column treated as a code that is actually free text will be
  passed through rather than scanned.
- **Is `PAT_ID` vs `PAT_MRN_ID` handled correctly for your organisation?** Both are tokenized here,
  but the distinction matters and varies by deployment.
- **Do the encounter-level identifiers (`PAT_ENC_CSN_ID`) leak anything through their key space**
  — for example, sequential or date-encoded CSNs?

### Q2. Do you agree with the Expert Determination reasoning?

The accelerator claims **Expert Determination**, not Safe Harbor, because it tokenizes 22 columns
with an HMAC of the source identifier. The argument is that §164.514(c)(1) forbids a
re-identification code *derived from* the individual, which an HMAC of an MRN is, so Safe Harbor is
unavailable — while HHS guidance §2.9 permits hash-derived values under Expert Determination.

See [docs/deidentification_standard.md](deidentification_standard.md). **If this reasoning is
wrong, the tool's headline claim is wrong.**

### Q3. Is the privacy-gate waiver mechanism honest, or is it a loophole?

`config/deid_rules.yaml` → `privacy_gates:` lets a named person accept a failing k-anonymity,
l-diversity, or t-closeness result, with a scope and an expiry. A waived run reports
`PASSED_WITH_ACCEPTED_RISK` rather than `PASSED`, and the signer appears in the evidence artifact.

The shipped default is `accepted_by: "UNSIGNED — repository default"`, scoped to synthetic data.

Please judge: **is this a defensible governance control, or does it just make it easy to ship a
failing configuration?** We believe the alternative — an unwaivable gate — gets deleted by the
first team it inconveniences, and that a recorded waiver is strictly better than the advisory
metric it replaced. That belief is worth challenging.

Related: §6.1 of [docs/deidentification_standard.md](deidentification_standard.md) shows that on
this dataset **no quasi-identifier configuration reaches k≥5**. Is our conclusion — that
generalization must be paired with suppression — the one you would draw?

### Q4. What would you *not* trust this with?

Where does the scope statement in [positioning_and_scope.md](positioning_and_scope.md) overclaim?
We would rather cut a claim than defend a shaky one.

## 3. Known gaps, already declared

Listing these so a reviewer does not spend time discovering what we already know:

- **Free-text recall is not benchmarked.** The scorecard hard-gates structured identifiers and
  reports contextual recall as `NOT_EVALUATED`. A defensible figure needs i2b2/n2c2, which is
  licensed and not redistributable.
- **The demo pepper is a committed literal.** Fine for synthetic data, fatal for real PHI. It is
  now blocklisted by digest and refused by `get_pepper()` unless a run sets
  `PHI_DEID_ALLOW_COMPROMISED_PEPPER="synthetic-data-only"`, and its use is stamped into the run
  manifest. The residual question for a reviewer is whether that acknowledgement is a control or
  a formality \u2014 see Q3. Details in [pepper_rotation_runbook.md](pepper_rotation_runbook.md) \u00a71a
  and [pre_real_phi_checklist.md](pre_real_phi_checklist.md).
- **HIPAA identifiers (P) biometrics and (Q) photographs are `NOT_EVALUATED`** — the pipeline reads
  structured Delta tables and has no imaging or biometric path.
- **RLS/CLS policies are written but not applied** in the reference deployment.

## 4. How to send findings

Open a GitHub issue, or annotate this file and send it back. Findings that contradict a claim in
the documentation are the most valuable thing you can produce — please do not soften them.
