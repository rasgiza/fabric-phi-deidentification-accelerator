# Pepper Rotation Runbook

The **pepper** is the single high-entropy secret that keys every token in the dataset. It
is fetched at runtime by `get_pepper()` from one of two sources:

- **Production (Option 1):** Azure Key Vault (public access disabled), read over a **Fabric
  managed private endpoint**. This is the target architecture and the only path used with
  real PHI.
- **Demo/synthetic (Option 2):** the `PHI_DEID_PEPPER` environment variable, for
  synthetic-data demos where no F-SKU capacity / managed private endpoint is available.
  `get_pepper()` prefers this env var when set, otherwise falls back to Key Vault.

This runbook covers generating, storing, versioning, and rotating the pepper.

> The pepper is not a decryption key — HMAC is one-way. Re-identification requires the
> Vault-only crosswalk, not the pepper. But the pepper *does* determine token values, so
> rotating it changes every token (that's the breach-recovery lever).

## 1. Generate a strong pepper

```python
import secrets
print(secrets.token_urlsafe(48))   # ~64 chars, 288 bits of entropy
```

`get_pepper()` rejects secrets shorter than 32 characters (`MIN_PEPPER_LENGTH`) to stop a
placeholder value from silently weakening the dataset.

## 2. Store it in Key Vault (production — Option 1)

> **Adopters:** the one-time vault + secret + role-grant setup is scripted in
> [`scripts/provision_keyvault.ps1`](../scripts/provision_keyvault.ps1) /
> [`scripts/provision_keyvault.sh`](../scripts/provision_keyvault.sh). It generates and
> stores the pepper without ever echoing it, and grants the runtime identity read access.
> The manual steps below are what those scripts automate.

```bash
az keyvault secret set \
  --vault-name <kv-name> \
  --name phi-deid-pepper \
  --value "<generated-pepper>"
```

- Grant the de-id notebook's managed identity **Key Vault Secrets User** (read-only) on the
  vault — nothing else needs `get`.
- Reach the vault over a **Fabric managed private endpoint** (public access stays disabled):
  create the MPE in the Fabric workspace, then approve the pending connection on the vault.
- Set the runtime URL via environment (never hardcode):
  `os.environ["PHI_DEID_KEYVAULT_URL"] = "https://<kv-name>.vault.azure.net/"`.

## 2b. Inject it via env var (synthetic demo — Option 2)

When running the accelerator on **synthetic data** without an F-SKU capacity / managed
private endpoint, skip Key Vault and supply the pepper directly:

```python
import os, secrets
os.environ["PHI_DEID_PEPPER"] = secrets.token_urlsafe(48)   # never print this value
```

- `get_pepper()` picks this up automatically and still enforces the 32-char minimum.
- Use the **same** pepper value in `02b_silver_deid` and `NB_reidentify`, or tokens won't
  round-trip. Generate once, set in both notebooks (or as a workspace env var).
- This path is for synthetic demos only and is **never** used with real PHI.

## 3. Version awareness

Key Vault versions every secret. Record the **secret version id** used for a run in the
audit manifest field `pepper_key_version` (see `audit.build_run_manifest(...,
pepper_key_version=...)`). This lets you prove which key produced a given `gold_safe_*`
output without ever storing the pepper itself.

## When to rotate

| Trigger | Action |
| --- | --- |
| Suspected pepper exposure / breach | **Emergency rotation** (below), treat as incident. |
| Personnel change with Vault access | Rotate; review Vault RBAC. |
| Scheduled hygiene | Rotate on your key-rotation policy cadence. |
| Re-tokenization requested (e.g. new sharing partner) | Rotate + re-run pipeline. |

## Rotation procedure

Rotating the pepper **invalidates all existing tokens and crosswalks**. Plan for a full
re-run.

1. **Freeze** writes to `gold_safe_*` and the Vault workspace.
2. **Generate** a new pepper (step 1) and set it as a **new version** of `phi-deid-pepper`.
3. **Re-run** `02b_silver_deid` → `03b_gold_safe` end to end so all tokens are recomputed
   under the new pepper. Capture the new `pepper_key_version` in the run manifest.
4. **Rebuild the crosswalk** via `NB_reidentify` (it recomputes tokens from raw silver under
   the new pepper). The old crosswalk is now stale — **retire it**.
5. **Run `NB_scorecard`** to confirm the new output passes the leak gate.
6. **Purge** old de-identified outputs and the stale crosswalk that were keyed to the old
   pepper (they no longer join to anything and are dead re-identification surface).
7. **Unfreeze** and record the rotation (date, reason, old/new version ids, approver).

## Guardrails

- Never print, `display()`, log, or write the pepper to a table, notebook output, or Git.
- Never keep two peppers "live" for the same dataset — tokens from different peppers won't
  join and create confusing partial linkage.
- The crosswalk is Vault-only; rotating the pepper does not relax that isolation.
