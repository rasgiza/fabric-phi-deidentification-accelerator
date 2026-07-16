# Contributing

Thanks for helping improve the Fabric PHI De-identification Accelerator. Because this code
governs how PHI is transformed, contributions are held to a high correctness + security bar.

## Ground rules

- **Synthetic data only.** Never commit real PHI, real peppers, crosswalk tables, or
  tenant-specific Key Vault URLs. CI runs secret scanning; do not disable it.
- **Preserve the invariants** in [SECURITY.md](SECURITY.md) (deny-by-default, namespace
  isolation, pepper hygiene, PHI-free audit artifacts). Add a test when you touch them.
- Keep the **pure-Python core** free of PySpark imports at module load — Spark stays lazy
  so the engine remains locally testable.

## Development setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # add ,spark to also run Spark integration tests: .[dev,spark]
```

## Before you open a PR — run the full local gate

```powershell
ruff check .                      # lint
ruff format --check .             # formatting
mypy                              # type-check (src)
pytest -m "not spark" --cov       # unit + property + config/audit/validation tests
bandit -r src                     # static security analysis
pip-audit                         # dependency CVEs
```

To also run the Spark path (requires Java + `pip install -e ".[dev,spark]"`):

```powershell
pytest -m spark
```

## Pull request checklist

- [ ] New/changed behavior is covered by tests (pure-Python where possible).
- [ ] `deid_rules.yaml` changes pass `validate_config` and were coverage-linted against the
      real schema (`audit_coverage`).
- [ ] No secrets, no real data, no hardcoded Key Vault URLs.
- [ ] Docs updated if the de-id strategy set, profiles, or workspace model changed.
- [ ] `CHANGELOG.md` updated under *Unreleased*.

## Commit / branch conventions

- Small, focused commits. Reference the invariant or doc you touched.
- A change to the strategy set, Safe Harbor mapping, or profile semantics should be
  reviewed by a code owner (see [CODEOWNERS](CODEOWNERS)).
