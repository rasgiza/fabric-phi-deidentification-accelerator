# Quickstart — 6 steps to a PHI-free Gold layer

> ⚠️ **SYNTHETIC DATA ONLY.** This is a reference pattern demonstrated on synthetic
> Epic-Caboodle data — **not** a certified de-identification service. Before any real PHI,
> work through [docs/pre_real_phi_checklist.md](docs/pre_real_phi_checklist.md).

**What you'll build:** a Bronze → Silver → Gold medallion on Microsoft Fabric where the
Gold layer feeding Power BI and Copilot contains **no PHI by construction**. The final
notebook (`NB_scorecard`) proves it by asserting **0 of the 18 HIPAA Safe Harbor
identifiers** survive into Gold.

**Time:** ~30–45 min for the synthetic happy path. **Cost:** just your Fabric capacity
(no Azure Key Vault needed for the synthetic demo).

## Two ways to set this up

- **Option A — One-click launcher (fastest).** Import
  [`PHI_Deid_Launcher.ipynb`](PHI_Deid_Launcher.ipynb) into any Fabric workspace and
  **Run All**. It creates the three workspaces + lakehouses, uploads `src/`, `config/`,
  and the sample data, and imports the notebooks — auto-patching the cross-workspace
  config so there are **no manual GUID edits**. You still set the pepper and run the
  notebooks (step 4–6 below). Requires permission to create workspaces.
- **Option B — Manual (full control).** Follow the 6 steps below and click through the
  portal yourself. Best if you want to see exactly what each piece does.

The launcher is a convenience wrapper around **the same artifacts** the manual steps use —
pick whichever you prefer.

## Prerequisites

- A **Microsoft Fabric** capacity (Trial capacity is fine) and permission to create workspaces.
- The **Data Engineering** experience enabled.
- This repo cloned locally:
  ```powershell
  git clone https://github.com/rasgiza/fabric-phi-deidentification-accelerator.git
  ```
- *(Production path only)* An Azure subscription for Key Vault. Not needed for the synthetic demo.

## The 6 steps

### 1. Create three workspaces
The accelerator's primary control is **physical isolation, not masking** — anyone with
Contributor+ on a workspace bypasses OneLake security/RLS/CLS, so PHI is *removed* to a
separate workspace, not hidden. Create three workspaces and attach a Lakehouse to each:

| Workspace | Audience | Notebooks |
|-----------|----------|-----------|
| **Raw (PHI)** | ~3 engineers | `01_bronze_ingest`, `02_silver_conform`, `02b_silver_deid` |
| **Analytics** | analysts, business, Copilot | `03b_gold_safe_analytics`, `NB_scorecard` |
| **Vault** | ~2 approvers (break-glass) | `NB_reidentify` |

### 2. Load the sample data (Raw workspace)
Upload the bundled folder [`sample_data/caboodle_provider/`](sample_data/caboodle_provider/)
(13 synthetic Caboodle CSVs — no real PHI) into the **Raw** Lakehouse at
`Files/raw/caboodle_provider/`.

Need more volume for load/variety testing? Append FK-safe synthetic rows:
```powershell
python scripts/generate_sample_data.py --add-claims 100000 --add-patients 5000 --seed 42
```

### 3. Import notebooks + upload the code package
Import each notebook into its workspace (**Data Engineering → Import notebook**), then upload
the repo's `src/` and `config/` folders into **each** workspace's Lakehouse at
`Files/accelerator/`.

### 4. Provide the tokenization pepper
- **Synthetic demo (easiest):** set the `PHI_DEID_PEPPER` environment variable — no Azure setup.
- **Production:** run [`scripts/provision_keyvault.ps1`](scripts/provision_keyvault.ps1),
  then set `PHI_DEID_KEYVAULT_URL` to your vault URL. See
  [docs/pepper_rotation_runbook.md](docs/pepper_rotation_runbook.md). Never hardcode it.

### 5. Run the notebooks in order
- **Raw:** `01_bronze_ingest` → `02_silver_conform` → `02b_silver_deid`
- **Analytics:** `03b_gold_safe_analytics` → `NB_scorecard`
- **Vault:** `NB_reidentify` only when a governed re-identification is approved.

`02b_silver_deid` is the **single privileged crossing point** — it runs in Raw, reads raw PHI,
and writes the de-identified copy that Analytics reads cross-workspace.

**Pick your HIPAA method (optional).** The accelerator ships both §164.514(b) methods as
profiles in [`config/deid_rules.yaml`](config/deid_rules.yaml): **Safe Harbor** (dates → year,
ZIP → 3-digit, age 90+ capped) and **Expert Determination** (per-patient date shift that
preserves intervals). It uses `active_profile` from that file by default. To switch at run
time without editing the YAML, set `PROFILE_OVERRIDE = "safe_harbor"` or
`"expert_determination"` in the selector cell near the top of `02b_silver_deid`.

### 6. Confirm the proof gate
`NB_scorecard` asserts **0/18 Safe Harbor identifiers** in `gold_safe_*` and writes a
PHI-free evidence artifact to `Files/audit/`. Green = the Gold layer is safe for Power BI
and Copilot.

### 7. (Optional) Open the Power BI report
A ready-made report + Direct Lake semantic model ship in [`reports/`](reports/). Open
`After PHI Deidentified.pbip` in Power BI Desktop, then make the **one** required edit:
point the model at your Analytics Lakehouse SQL endpoint (placeholder
`REPLACE_WITH_YOUR_SQL_ENDPOINT` in
[`Gold Safe Analytics.SemanticModel/definition/expressions.tmdl`](reports/Gold%20Safe%20Analytics.SemanticModel/definition/expressions.tmdl)).
Full steps — including the optional "before" reports — are in [reports/README.md](reports/README.md).

## Cleanup
Delete the three Fabric workspaces (this removes their Lakehouses and all `bronze_*`,
`silver_*`, `silver_deid_*`, `gold_safe_*`, and `xwalk_*` tables). For the production path,
also delete the Key Vault / resource group you provisioned in step 4.

## Where to go next
- Full narrative + design rationale: [README.md](README.md)
- Security isolation model: [docs/security_model.md](docs/security_model.md)
- Guided demo (1 admin + 2 users): [docs/demo_runbook.md](docs/demo_runbook.md)
- Before real PHI: [docs/pre_real_phi_checklist.md](docs/pre_real_phi_checklist.md)
