# Demo Runbook — PHI De-Identification on Fabric

> **SYNTHETIC DATA ONLY.** A 6-act narrative for the customer room (Ed, Arun, Josh, Brian,
> Ben, Trinity, Kiley). One admin account + two user accounts.

## Cast & accounts

| Account | Persona | **Workspace role** (Analytics) | OneLake security role | Sees |
|---------|---------|-------------------------------|-----------------------|------|
| **Admin** (you / Michael) | Platform/data engineer | **Contributor** (or Member) — *builds* items | n/a (bypasses data plane) | Everything: Raw, Analytics, Vault |
| **User A — Analyst** | Business analyst | **Viewer** — never Contributor | `analyst_deid` | `gold_safe_*` only, scoped: no MRN/NPI, own region |
| **User B — Steward/Security** | Data steward / EIS proxy (Arun) | **Viewer** (+ role) | `data_steward` | `gold_safe_*` incl. tokens, all regions |

> ⚠️ **Critical governance rule — workspace roles override OneLake security.** Anyone who is
> **Admin / Member / Contributor** on the Analytics workspace **bypasses** the data plane —
> OneLake security, column DENY, and row filters do **not** apply to them. Only **Viewer** (or
> access granted *purely* through a OneLake data-access role, with no workspace role) is
> subject to those restrictions. So: **builders stay Contributor** (Admin/Michael); the
> **analyst persona (User A) MUST be a Viewer** — if you already shared the workspace with User
> A as Contributor, change them to **Viewer** in *Manage access* before the demo, or the Act 5
> scoping won't fire. This is *why* de-identification (bytes removed) is the primary control:
> even a bypassing Contributor sees no real PHI in `gold_safe_*` — there's none left to see.

## Pre-demo setup

> **Workspace placement matters.** These notebooks are split across the three workspaces
> (Raw / Analytics / Vault) by design — `02b_silver_deid` runs in **Raw** (the one privileged
> crossing point), `03b_gold_safe_analytics` + `NB_scorecard` run in **Analytics**, and
> `NB_reidentify` lives in **Vault**. See the workspace/notebook table in the
> [README Quickstart (Fabric)](../README.md#quickstart-fabric) and the rationale in
> [docs/security_model.md](security_model.md).

1. Land the 13 synthetic Caboodle CSVs at `Files/raw/caboodle_provider/`.
2. Upload the accelerator `src/` and `config/` to `Files/accelerator/`.
3. Store the pepper in Key Vault (`phi-deid-pepper`).
4. Run `01` → `02` (foundation) and `03_gold_star` (**before** — writes `gold_*` with raw
   PHI into `lh_raw`) so you can toggle live.
5. Produce the **after** layer where it will be governed: attach **`lh_analytic`** as the
   default lakehouse and upload the accelerator `src/`+`config/` to its `Files/accelerator/`,
   then run `02b_silver_deid` (in PHI-Raw — the privileged crossing point) and
   `03b_gold_safe_analytics`. That notebook reads `silver_deid_*` cross-workspace from
   PHI-Raw `lh_raw` and writes `gold_safe_*` **natively into `lh_analytic`** — one physical
   copy, no shortcuts, so OneLake security can scope it in Act 4.
6. Bind two semantic-model variants: one on `gold_*` (Raw), one on `gold_safe_*` (Analytics).

## Act 1 — The problem (2 min)

As **Admin**, open `03_gold_star`'s `gold_dim_patient`. Show that **raw `MRN`,
`PatientName`, `DateOfBirth`, and full `ZIP`** have reached Gold — the layer Power BI and
Copilot read. *"This is the customer's own pipeline today: four Safe Harbor identifiers sit
in the AI layer."* This is the fear in the room, made concrete.

## Act 2 — Catalog & classification (Tier 0) (5 min)

As **Steward**, in the OneLake Catalog: find the dataset, apply Purview sensitivity labels to
the PHI columns. Frame it with the PG positioning — *"Fabric is Microsoft's primary data
governance solution, and the OneLake catalog is the unified foundation to discover, manage,
and govern data across multi-cloud and hybrid environments."* Then make the two EIS points
explicitly (Arun's questions):
- Cataloging = **discovery-only**; it leaks no data.
- A label is a **classification stamp**, not enforcement; OneLake security enforces the data
  plane, **role-scoped, not tenant-wide**.
Tie it forward: *"these labels are the rulebook for the de-id engine."*

## Act 3 — De-identify (Tier 3) (8 min)

As **Admin**, run `02b_silver_deid` (PHI-Raw — the single privileged crossing point) then
`03b_gold_safe_analytics`. Narrate the strategies as counts scroll (no raw data is ever
shown — that's the point). Call out the topology: the gold notebook **reads** the PHI-free
`silver_deid_*` cross-workspace from PHI-Raw `lh_raw` and **writes** `gold_safe_*` natively
into **`lh_analytic`** — raw PHI never enters Analytics, and there's exactly one physical
copy (no shortcut) for OneLake security to govern next. Then open `gold_safe_dim_patient`:
- `MRN` → `PT-…` token; **joins still work** (show a fact join by `PatientKey`).
- `PatientName` → synthetic; `DateOfBirth` → `BirthYear`; `ZIP` → 3 digits; `Age` capped 90.
Run `NB_scorecard` → **PASS**: 0/18 identifiers in Gold.

## Act 4 — Govern access with OneLake security (data plane) (5 min)

As **Steward (User B)**, open `lh_analytic` → **Manage OneLake data access**. Because
`gold_safe_*` physically lives here (Act 3), the tables are **selectable** in the role editor
— a shortcut would be greyed out (enforcement defers to the source lakehouse). Build the two
roles live:

- **`analyst_deid`** — Grant **Read** on the `gold_safe_*` tables; assign **User A**. This is
  the analyst's data-plane grant, **role-scoped, not tenant-wide**.
- **`data_steward`** — Grant **Read** on the same tables incl. token columns; assign
  **User B**.

Make the EIS point: OneLake security is the **enforcement** layer (data plane), distinct from
the Catalog (discovery) and labels (classification) from Act 2. Then land the sharp caveat
that sells de-identification: **workspace Admin/Member/Contributor BYPASS these roles.** *"If
Michael here is a workspace Contributor, none of these rules touch him — he sees every column
and every row. That's by design: builders are trusted with their own workspace. Which is
exactly why we don't rely on hiding PHI — we removed it. Even a bypassing Contributor finds
no real MRN in `gold_safe_*`."* Only the **Viewer** analyst (User A) is scoped. **Column**
(MRN/NPI DENY) and **row** (region) rules are finer-grained than the table-level UI, so
they're applied on the `lh_analytic` **SQL analytics endpoint** via
[`rls_cls_policies.sql`](../src/sql/rls_cls_policies.sql) — show that file briefly; you'll
prove it live in Act 5.

## Act 5 — Access & re-identification (5 min)

- As **Analyst (User A)**, open the report on `gold_safe_*` in **Analytics** — full
  analytics, zero PHI, reaching only the tables the `analyst_deid` role granted in Act 4. Try
  `SELECT MRN …` on the `lh_analytic` SQL endpoint → **denied** by the column DENY in
  [`rls_cls_policies.sql`](../src/sql/rls_cls_policies.sql); show region-scoped RLS (analyst
  sees only their region's facilities).
- As **Admin**, briefly show `NB_reidentify` lives in the **Vault** workspace only, ~2
  people, HMAC-irreversible without the crosswalk, every use audited. Don't dwell — it's the
  break-glass exception, not the norm.

## Act 6 — Why it matters (3 min)

- The Gold layer is **not PHI** → safe for **Copilot / AI** without BAA-scoped constraints.
- Everything ran **in-tenant** on **Microsoft-native** parts (Spark + Key Vault + OneLake
  security). No data left Fabric.
- Land the ladder: *"Start at Tier 0 today; Tier 3 is the north-star this unlocks."* Note
  Tonic (Azure Marketplace, MACC-eligible) as the complementary buy for free-text/synthetic.

## FAQ likely from the room

- **"Is Fabric HIPAA compliant?"** → Shared responsibility; Fabric is HIPAA-capable under the
  BAA. See [hipaa_compliance.md](hipaa_compliance.md).
- **"Can an admin still see PHI in Gold?"** → There is no PHI in Gold to see — it was removed,
  not hidden. Raw PHI is isolated in the Raw workspace.
- **"Does the catalog expose our data tenant-wide?"** → No; discovery-only. See the
  [EIS one-pager](../tier0/eis_security_one_pager.md).
- **"What about doctors' notes / free text?"** → NER (Presidio/Tonic Textual) plugs into the
  same engine; shown as a roadmap slot.
