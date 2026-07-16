# Adopter setup scripts

One-time provisioning for teams running this accelerator in **their own** tenant/workspace.

## `provision_keyvault.ps1` / `provision_keyvault.sh`

Creates (or reuses) an RBAC-enabled Key Vault, generates a high-entropy tokenization
**pepper**, stores it as the secret `phi-deid-pepper`, and grants the identity that runs the
de-id notebook read-only access (`Key Vault Secrets User`). The pepper value is generated
locally and written straight to the vault — it is **never printed, logged, or committed**.

You only need this for the **production path (Option 1)**. For synthetic-data demos, skip it
and set `PHI_DEID_PEPPER` instead (Option 2). See
[../docs/pepper_rotation_runbook.md](../docs/pepper_rotation_runbook.md).

### The one decision that matters: which identity to grant

The notebook reads the secret as **whatever identity triggered the run**, not as whoever
created the vault:

| How you run the gold notebook | `-GranteeObjectId` / `--grantee-object-id` |
|---|---|
| Interactive (a person clicks Run) | The **user's** object ID — `az ad signed-in-user show --query id -o tsv` |
| Scheduled / Data Pipeline | The **workspace managed identity** object ID (Fabric workspace → Workspace settings → Managed identity) |

### PowerShell

```powershell
./provision_keyvault.ps1 `
  -VaultName kv-phideid-contoso `
  -ResourceGroup rg-phi-deid `
  -GranteeObjectId (az ad signed-in-user show --query id -o tsv)
```

### Bash

```bash
./provision_keyvault.sh \
  --vault-name kv-phideid-contoso \
  --resource-group rg-phi-deid \
  --grantee-object-id "$(az ad signed-in-user show --query id -o tsv)"
```

### Real PHI

Add `-DisablePublicAccess` (PowerShell) / `--disable-public-access` (bash) to lock the vault
down, then create a **Fabric managed private endpoint** to it and approve the pending
connection. Real PHI must never traverse a public vault endpoint.

## `generate_sample_data.py`

Appends more **synthetic** rows to the bundled Caboodle dataset in
[`../sample_data/caboodle_provider/`](../sample_data/caboodle_provider/) while keeping every
foreign key valid. Curated dimension/provider tables are treated as fixed reference data; only
patients and the fact tables (claims, encounters, risk scores) grow, referencing existing keys.
Standard library only — no `pip install`.

```bash
# Append 100k claims + 5k patients, reproducibly
python scripts/generate_sample_data.py --add-claims 100000 --add-patients 5000 --seed 42

# Grow every fact table a bit, against a copy of the data
python scripts/generate_sample_data.py --data-dir /tmp/mydata \
  --add-patients 2000 --add-claims 20000 --add-encounters 10000 --add-risk-scores 5000
```

Everything it emits is synthetic — it never produces real PHI. Run `--help` for all options.

