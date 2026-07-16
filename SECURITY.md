# Security Policy

This project handles the *pattern* for de-identifying Protected Health Information (PHI).
Security defects here can translate directly into re-identification or PHI exposure, so we
treat them with the highest priority.

## Reporting a vulnerability

**Do not open a public GitHub issue for a security vulnerability.**

Report privately to the security contact listed in [CODEOWNERS](CODEOWNERS) (or your
organization's security intake / `security@<your-org>`). Include:

- A description of the issue and its impact (e.g. potential re-identification vector).
- Steps to reproduce, affected files/functions, and any proof-of-concept.
- Whether any real PHI was involved (if so, follow your incident-response process **first**).

You will receive an acknowledgement within 3 business days.

## Critical invariants — treat a break in any of these as a security bug

1. **The pepper never leaves Key Vault into code, logs, notebook output, or Git.**
   `get_pepper()` refuses placeholder/short secrets; nothing prints the pepper.
2. **Deny-by-default.** Any unclassified column must be suppressed, never emitted.
   A change that causes an unknown column to pass through is a security regression.
3. **Namespace isolation.** Tokens for different columns must not collide
   (`tokenize(v, ns="mrn") != tokenize(v, ns="npi")`).
4. **The crosswalk is Vault-only.** Re-identification material (`xwalk_*`,
   `NB_reidentify`) must never be reachable from the Analytics workspace.
5. **No PHI in audit artifacts.** Run manifests and logs carry counts/metadata only.

## Handling secrets

- Pepper and any credentials live in **Azure Key Vault**; reference by name at runtime.
- The Key Vault URL is supplied via the `PHI_DEID_KEYVAULT_URL` env var — never hardcoded.
- Secret scanning (gitleaks) and dependency auditing (pip-audit) run in CI; do not disable.

## Supported scope

This is a synthetic-data reference accelerator. Security fixes are provided for the
`main` branch. There is no warranty of HIPAA compliance — see `docs/hipaa_compliance.md`.
