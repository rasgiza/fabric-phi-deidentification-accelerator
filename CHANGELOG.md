# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **All 17 gold tables are now in the semantic model.** The seven `gold_safe_*_clarity_*`
  tables were being built and published on every run and were invisible in Power BI: the
  TMDL in `reports/` was hand-authored and nothing tied it back to the star declaration,
  so half the accelerator's output had no way to reach a report. Nothing failed — the
  notebooks were green, the tables were there, the report just quietly showed the Caboodle
  half.
  - Every Clarity fact joins `gold_safe_dim_patient[PatientKey]` — the *same* dimension the
    Caboodle facts use — so one patient is one row regardless of how many systems they
    appear in, and any fact can be sliced by `SourceSystem`.
  - Clarity's `DEPARTMENT_ID` and `PAT_ENC_CSN_ID` are deliberately **not** related to the
    Caboodle dimensions: those are different key spaces that merely describe similar things,
    and joining them would fabricate conformance the extracts do not have. For the same
    reason `fact_clarity_result` is not related to `fact_clarity_order_proc` — a fact-to-fact
    relationship lets filters cross grains and double-count.
  - `gold_safe_dim_patient` gained the `ClarityPatientID` column it has been publishing all
    along, plus **Patients**, **Patients in Both Systems** and **Cross-Source Match %**
    measures. The second is a *subset* of the first, never an addition — matched patients
    are one row, not two.
  - All 12 new relationships were verified against live data: zero orphan keys, zero nulls,
    unique keys on both dimension sides.
- **`scripts/generate_clarity_semantic_tables.py`** renders the Clarity half of the model
  from `gold_conform.CLARITY_GOLD`, the same declaration the notebooks build from, so the
  model can no longer drift from the tables it binds. `lineageTag` GUIDs are derived with
  `uuid5` rather than randomly, so regenerating produces no spurious diff and does not break
  report bindings. `tests/test_semantic_model.py` fails the build on drift — adding a gold
  table without regenerating is now a red CI run rather than a silently incomplete model.
- **`NB_cleanup_gold` is implemented** (it shipped as an empty placeholder). It reconciles
  the `gold_safe_*` tables that exist against the ones `GOLD_TABLES` declares and drops the
  difference. The point is not tidiness: this accelerator's privacy guarantees are per-run
  and per-table, so a table left behind by an earlier configuration is one that nothing
  rewrote and therefore nothing re-scanned — produced under whatever rulebook was active at
  the time, still bound by the semantic model, still queryable through the SQL endpoint, and
  looking exactly as trustworthy as a current table. Orphans appear routinely and quietly
  whenever an adopter drops a source, renames a table, or trims the declaration.
  - Deny-by-default: `CONFIRM = False` prints the plan and drops nothing. `MODE` selects
    orphans-only (routine) or a full teardown (after a schema change).
  - Hard-scoped to the `gold_safe_` prefix with an explicit refusal if a target ever falls
    outside it, so it cannot reach bronze, silver, or the identified gold star.
  - `MODE`/`CONFIRM` are a Fabric parameters cell, so a pipeline can run it on a schedule in
    report-only mode and alert on orphans without permission to drop anything.
  - Both paths verified in Fabric against a planted orphan: the dry run left all 18 tables
    intact; the confirmed run dropped exactly the orphan and left all 17 declared tables.

- **Clarity is now conformed into the Gold star** (`03b_gold_safe` and its Analytics twin),
  closing the gap where Clarity was de-identified and governed but never reached Power BI:
  - `gold_safe_dim_patient` becomes a **conformed dimension**. The two schemas are matched on
    the shared `mrn` token namespace, so a patient in both systems is **one row** tagged
    `SourceSystem = 'Both'` rather than two. A Clarity-only patient's `PatientKey` is minted
    deterministically from that same token, so they keep their key if they later appear in
    Caboodle — a random surrogate would have forked them into a second row.
  - Clarity's orders, results, diagnoses and admissions land as `gold_safe_fact_clarity_*` at
    **their own grain**, linked through the conformed `PatientKey`. They are deliberately not
    unioned into `fact_encounter`: that would invent a grain neither source actually has.
  - New `src/fabric_phi_deid/gold_conform.py` holds the star's column projections
    declaratively and **Spark-free**, so the PHI-Raw and Analytics copies of `03b` cannot
    drift apart and CI can test the model's shape on a laptop.
  - New `tests/test_gold_conform.py` asserts every published column is explicitly ruled and
    **not suppressed** (a suppressed column publishes as silent nulls, which looks like
    missing data rather than a bug), that both MRN columns still share one token namespace,
    and that every Clarity fact resolves through a consistently tokenized `PAT_ID`.
  - The publish gate gained **conformed-key integrity checks**: it now fails closed on a
    duplicate `PatientKey` (which would fan out every measure joined through the patient
    dimension) and on any Clarity fact whose `PatientKey` failed to resolve (which would drop
    out of every cohort filter while still inflating unfiltered totals). Row-count
    reconciliation now compares against the count the build **declared** it intended to
    publish, which is what allows `dim_patient` to legitimately union two sources.
  - A partially de-identified Clarity source is now a hard failure in `03b` rather than a
    silent half-build, mirroring `02b`.
  - `gold_safe_dim_patient.SourceSystem` is surfaced in the semantic model so a report can
    slice Caboodle vs Clarity vs Both.
- **Second synthetic source schema: Epic Clarity** (`sample_data/Clarity/`, 24 normalized
  transactional CSVs) alongside the existing Caboodle dimensional set, proving that
  onboarding another EHR schema is a **config change, not a code change**:
  - `config/deid_rules.yaml` gains 8 `clarity_*` table blocks in **both** profiles.
    `PAT_MRN_ID` shares the `mrn` token namespace with Caboodle's `MRN`, so the same patient
    resolves to the same token across schemas. `PAT_ENC_CSN_ID` is tokenized (a CSN is
    printed on paperwork) while Caboodle's internal `EncounterKey` stays passthrough.
  - `01_bronze_ingest` gains a `SOURCES` registry plus **header, parse, and primary-key
    contract checks** — a renamed or reordered source column now fails the run instead of
    silently routing a PHI value through the wrong rule.
  - `02_silver_conform` derives `AGE`/`AGE_BAND` from shared helpers (so a cross-source
    cohort cannot compare two different definitions of "65+"), broadcasts the patient key
    set for referential-integrity filters, and discovers reference tables from the catalog.
  - `02b_silver_deid` now contains **no table names**: it resolves whichever sources are
    present and treats a *partially* loaded source as a hard failure.
  - `PHI_Deid_Launcher.ipynb` uploads both datasets from a registry mirroring `01`'s.
  - `tests/test_multi_source_rules.py`: profile parity, cross-schema token linkage,
    "no direct identifier is passthrough", "Safe Harbor never emits a full date", and
    "Expert Determination shifts dates by patient".

### Changed
- **`PHI_Deid_Launcher.ipynb` hardened for unattended runs.** Every Fabric REST call now
  goes through a shared `requests.Session` with an explicit `(connect, read)` timeout and a
  bounded retry policy. `requests` has **no default timeout**, so a stalled connection
  previously hung the notebook — and its Spark session — indefinitely. Retries fire on
  status only (`read=0`, `status_forcelist=(429, 502, 503, 504)`, honouring `Retry-After`):
  those responses mean the request was throttled or never reached the service, so replaying
  is safe, whereas a read timeout *after* a POST was accepted is deliberately **not**
  retried — item creation is not idempotent and a blind replay would duplicate notebooks.

### Fixed
- **Launcher repo download no longer extracts an archive unchecked.** Members are validated
  against the extraction root before `extractall` (zip-slip / path traversal), the archive is
  unpacked into `tempfile.mkdtemp()` instead of a predictable `/tmp` path (symlink and
  pre-creation attacks; also lets two runs coexist), and prerequisite checks raise
  `RuntimeError` rather than `assert` — assertions vanish under `python -O`.
- Bundled synthetic sample dataset under `sample_data/caboodle_provider/` — the 13 Caboodle
  provider CSVs (generated with Tonic Fabricate, no real PHI) so the Bronze→Silver→Gold
  pipeline runs immediately after clone.
- Portable Power BI assets under `reports/`: a committed **Direct Lake semantic model**
  (`Gold Safe Analytics.SemanticModel`, TMDL) over the `gold_safe_*` tables, with the
  `After PHI Deidentified` report bound to it **by path** so the PBIP opens self-contained.
  Tenant-specific values (SQL endpoint, semantic-model GUIDs) are replaced with documented
  placeholders; `reports/README.md` covers the one-edit rebind. The `Before`/`Toggle`
  baseline reports carry placeholder connections for the user's own `gold_*` model.
- `scripts/generate_sample_data.py`: standard-library generator that appends more **synthetic**
  patients and fact rows (claims, encounters, risk scores) to the bundled dataset while
  preserving referential integrity to existing dimension/provider keys.
- `determination.py`: **Expert Determination evidence pack** — bundles config fingerprint,
  k-anonymity / l-diversity / t-closeness measurements, and the residual direct-identifier
  scan into one PHI-free artifact with a single `passes` gate and a time-limited review-by
  date. Renders to JSON and markdown for a reviewer's record (`build_determination_report`,
  `DeterminationReport`, `ResidualScanResult`, `residual_scan_from_hits`).
- `scripts/provision_keyvault.ps1` / `.sh`: one-time, parameterized Key Vault provisioning
  for adopters (create/reuse RBAC vault, generate + store the pepper without echoing it,
  grant `Key Vault Secrets User` to the chosen runtime identity, optional public-access
  lockdown for real PHI). Documents the user-vs-workspace-managed-identity choice.
- CI: CodeQL (`security-and-quality`) analysis workflow; tag-driven `release.yml` that
  verifies the tag matches `__version__`, builds sdist+wheel, and attaches them to a Release.
- CI coverage gate: `--cov-fail-under=70` to prevent silent regression.
- Installable package layout (`src/fabric_phi_deid/`) with `pyproject.toml` (hatchling).
- `config.py`: schema `validate_config` (fail-fast in `load_rules`) and `audit_coverage`
  coverage linter (flags defaulted/missing columns vs. real schema).
- `audit.py`: `config_fingerprint`, PHI-free `RunManifest` / `build_run_manifest`, and a
  PHI-safe `get_audit_logger`.
- `validation.py`: reusable PHI leak scanner (`scan_value_for_phi`, `PHI_PATTERNS`) plus a
  Spark-side `scan_spark_dataframe` for the scorecard gate.
- Tests: config-validation, audit, validation, Hypothesis property tests, and PySpark
  end-to-end integration tests (marked `spark`).
- CI: GitHub Actions (ruff, mypy, pytest+coverage, bandit, pip-audit, gitleaks) and
  Dependabot.
- Governance: `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS`, `.gitignore`.
- Docs: pepper-rotation runbook and pre-real-PHI checklist.

### Changed
- `get_pepper()` now resolves the Key Vault URL from `PHI_DEID_KEYVAULT_URL` (or an argument)
  instead of a hardcoded placeholder, and rejects short/placeholder peppers.
- `load_rules()` now validates the config and raises `ConfigValidationError` on any problem
  before touching data.
- Notebooks and tests import from the `fabric_phi_deid` package.

## [0.1.0] - initial

- Two-tier PHI de-identification accelerator: tokenization (keyed HMAC), strategy engine
  (tokenize/synthesize/generalize/date_shift/suppress/passthrough), `deid_rules.yaml`
  profiles (safe_harbor / expert_determination), medallion notebooks, RLS/CLS SQL, Tier-0
  catalog assets, and documentation.
