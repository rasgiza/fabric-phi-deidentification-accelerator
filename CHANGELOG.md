# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Bundled synthetic sample dataset under `sample_data/caboodle_provider/` — the 13 Caboodle
  provider CSVs (generated with Tonic Fabricate, no real PHI) so the Bronze→Silver→Gold
  pipeline runs immediately after clone.
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
