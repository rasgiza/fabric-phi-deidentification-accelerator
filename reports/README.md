# Power BI reports & semantic model

These are **thin-report** PBIP items plus one committed **semantic model**, used by the
demo to show the same analytics *before* vs *after* de-identification. They run on
**synthetic** data only.

> Workspace names (`PHI-Raw`, `PHI-Analytics`) and any Lakehouse names (`lh_analytics`)
> in these files are **examples from the reference environment**. Yours will differ —
> see the rebind steps below.

## What's here

| Item | Type | Binds to | Portable? |
|------|------|----------|-----------|
| `Gold Safe Analytics.SemanticModel` | Semantic model (Direct Lake, TMDL) | your `gold_safe_*` tables | ✅ committed — 1 endpoint edit |
| `After PHI Deidentified.pbip` / `.Report` | Report | the model above (**byPath**) | ✅ opens with the model |
| `Before PHI Exposed.pbip` / `.Report` | Report | your own `gold_*` (PHI) model | ⚠️ rebind required |
| `PHI Toggle Demo.pbip` / `.Report` | Report | your own `gold_*` (PHI) model | ⚠️ rebind required |

The **"After"** report is the important one — it proves full analytics on a **PHI-free**
Gold layer. It ships with its semantic model so it opens as a self-contained PBIP. The
**"Before"/"Toggle"** reports demonstrate the *unsafe* baseline (PHI reaching Gold); their
model (`gold_*`, built by notebook `03_gold_star`) is environment-specific and **not**
shipped, so they carry a placeholder connection you must rebind.

## The one unavoidable edit — point the model at your Lakehouse

The semantic model is **Direct Lake**, so it must reference *your* Analytics Lakehouse SQL
endpoint. Open
[`Gold Safe Analytics.SemanticModel/definition/expressions.tmdl`](Gold%20Safe%20Analytics.SemanticModel/definition/expressions.tmdl)
and replace the placeholder:

```
Source = Sql.Database("REPLACE_WITH_YOUR_SQL_ENDPOINT.datawarehouse.fabric.microsoft.com", "lh_analytics")
```

- **SQL endpoint** — in Fabric, open your **PHI-Analytics** Lakehouse → **Settings → SQL
  analytics endpoint → SQL connection string**. Paste that host in place of
  `REPLACE_WITH_YOUR_SQL_ENDPOINT.datawarehouse.fabric.microsoft.com`.
- **Lakehouse name** — replace `lh_analytics` if your Analytics Lakehouse is named
  differently.

The model's tables are already named `gold_safe_dim_*` / `gold_safe_fact_*` to match the
output of notebook `03b_gold_safe_analytics`, so no table remapping is needed.

## What's in the model

All **17** gold tables are bound, in two halves that meet at one shared dimension:

| Half | Tables | Grain |
|------|--------|-------|
| **Caboodle** (dimensional) | `dim_provider`, `dim_department`, `dim_facility`, `dim_payer`, `dim_diagnosis`, `dim_procedure`, `fact_encounter`, `fact_claim`, `fact_risk_score` | surrogate-key star |
| **Clarity** (transactional) | `dim_clarity_provider`, `fact_clarity_encounter`, `fact_clarity_admission`, `fact_clarity_diagnosis`, `fact_clarity_order_med`, `fact_clarity_order_proc`, `fact_clarity_result` | Epic-native IDs |
| **Shared** | `dim_patient` | one row per person, across both |

**Patient is the only conformed dimension** — and that is deliberate. Every Clarity fact
joins `gold_safe_dim_patient[PatientKey]`, the same dimension the Caboodle facts use, so
one patient is one row no matter how many systems they appear in. Slice any fact by
`dim_patient[SourceSystem]` (`Caboodle` / `Clarity` / `Both`) and the counts reconcile.

What is deliberately **not** joined:

- **Clarity `DEPARTMENT_ID` → `dim_department`.** Different key spaces that happen to
  describe similar things. Relating them would fabricate conformance the extracts do not
  have and produce joins that look right and are wrong.
- **Clarity `PAT_ENC_CSN_ID` → `fact_encounter`.** Same reason; the two sources do not
  share an encounter identifier.
- **`fact_clarity_result` → `fact_clarity_order_proc`.** Fact-to-fact relationships let
  filters cross grains and double-count. Conform through the shared dimension instead.

Three measures on `dim_patient` surface the cross-source claim directly: **Patients**,
**Patients in Both Systems**, and **Cross-Source Match %**. The second is a *subset* of
the first, never an addition to it — matched patients are one row, not two. If
**Patients in Both Systems** reads 0, the cross-source join silently failed; `03b`
asserts the same match rate at build time and will stop before publishing.

## Changing the model

The Clarity half is **generated** from `gold_conform.CLARITY_GOLD` — the same declaration
the notebooks build from — so the model cannot drift from the tables it binds:

```bash
python scripts/generate_clarity_semantic_tables.py           # regenerate
python scripts/generate_clarity_semantic_tables.py --check   # CI: fail on drift
```

`tests/test_semantic_model.py` enforces this. Add a gold table without regenerating, and
the build fails instead of shipping a model that quietly omits a fact table — which is
exactly how the seven Clarity tables came to be published to the lakehouse and invisible
in Power BI. `lineageTag` GUIDs are derived deterministically, so regenerating produces no
spurious diff and does not break report bindings.

The Caboodle half is hand-authored and edited directly.

## Open / deploy

**Power BI Desktop (recommended):** open `After PHI Deidentified.pbip`. The report loads
with its committed model; after the endpoint edit above, refresh to see data.

**Publish to Fabric:** either publish from Desktop into your **PHI-Analytics** workspace,
or use Fabric **Git integration** / the deploy pipeline to sync the `reports/` folder.

## Rebinding the "Before" / "Toggle" reports (optional)

These show the *unsafe* baseline and need a model over the PHI-carrying `gold_*` tables
(from notebook `03_gold_star`, in **PHI-Raw**):

1. Build a semantic model on your `gold_*` tables (Direct Lake, same as above).
2. Open `Before PHI Exposed.Report/definition.pbir` (and the Toggle one) and replace
   `semanticmodelid=REPLACE_WITH_YOUR_SEMANTIC_MODEL_ID` with your model's ID — or, in
   Power BI Desktop, **Transform data → Data source settings** and repoint the connection.

Prefer not to bother? The **"After"** report alone is enough to demonstrate the safe
consumption layer.
