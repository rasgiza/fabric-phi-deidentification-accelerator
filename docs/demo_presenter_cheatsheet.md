# Presenter Cheat-Sheet — PHI De-ID Demo (Admin + 2 Users)

> **SYNTHETIC DATA ONLY.** Companion to [demo_runbook.md](demo_runbook.md). Live Fabric
> tenant, three real Entra identities. Keep this open on a second screen while you present.

---

## Identity setup — three browser profiles (switch in ~2 sec, no re-login)

| Window | Profile / account | Scoped to | Keep open on |
|--------|-------------------|-----------|--------------|
| **1 — Admin** (you) | Your platform account | Raw + Analytics + Vault | Notebook `03_gold_star` / `03b_gold_safe` |
| **2 — Analyst (User A)** | Secondary Entra acct | Analytics workspace only | Power BI report on `gold_safe_*` |
| **3 — Steward (User B)** | Secondary Entra acct | Catalog + OneLake security roles | OneLake Catalog view |

Arrange side-by-side (or 3 virtual desktops). The visible identity change **is** the proof.

---

## Pre-demo setup checklist (do the day before, verify 30 min before)

**Data & code**
- [ ] Synthetic CSVs landed: Caboodle (13) at `Files/raw/caboodle_provider/`, Clarity (24) at `Files/raw/clarity/`
- [ ] Accelerator `src/` + `config/` uploaded to `Files/accelerator/`
- [ ] Pepper stored in Key Vault as `phi-deid-pepper`; KV URL wired via `PHI_DEID_KEYVAULT_URL`

**Pre-run both states (never compute live)**
- [ ] `01_bronze_ingest` → `02_silver_conform` (foundation)
- [ ] `03_gold_star` — the **BEFORE** (PHI in Gold)
- [ ] `02b_silver_deid` → `03b_gold_safe` — the **AFTER** (PHI-free Gold)
- [ ] `NB_scorecard` runs clean and returns **PASS (0/18)** — this is the only live run

**Access & reports**
- [ ] Two semantic-model variants bound: one on `gold_*`, one on `gold_safe_*`
- [ ] Analyst (User A) can open the `gold_safe_*` report; **cannot** see `gold_*`
- [ ] `sql/rls_cls_policies.sql` applied — `SELECT MRN …` as Analyst is **denied**
- [ ] Steward (User B) can see the dataset + labels in OneLake Catalog
- [ ] `NB_reidentify` lives in the **Vault** workspace only (verify Analyst can't see it)

**Room readiness**
- [ ] All 3 browser profiles logged in and pinned; test the switch once
- [ ] Purview sensitivity labels ready to apply live in Act 2 (or pre-applied as backup)
- [ ] Rehearse the exact `SELECT MRN` denial query so it fails instantly on stage

---

## One-page run sheet — 23 min, act by act

| # | Time | Identity | Click | Say (the line) |
|---|------|----------|-------|----------------|
| **1 — Problem** | 2m | Admin | Open `03_gold_star` → `gold_dim_patient` | *"This is their pipeline today — raw MRN, name, DOB, full ZIP have reached Gold, the layer Copilot reads. Four Safe Harbor identifiers sitting in the AI layer."* |
| **2 — Catalog** | 5m | **Steward** | OneLake Catalog → find dataset → apply Purview labels to PHI cols | *"Fabric is Microsoft's primary data governance solution; the OneLake catalog is the unified foundation to discover, manage & govern across multi-cloud and hybrid."* Then the 2 EIS points: **catalog = discovery-only, no data leak**; **a label is a stamp, not enforcement — OneLake security enforces, role-scoped not tenant-wide.** *"These labels are the rulebook for the de-id engine."* |
| **3 — De-identify** | 8m | Admin | Run `02b_silver_deid` → `03b_gold_safe` (or show pre-run); open `gold_safe_dim_patient`; run **`NB_scorecard` live** | Narrate counts (no raw data shown — *that's the point*): MRN→`PT-…` token (**joins still work** — show fact join on `PatientKey`), name→synthetic, DOB→BirthYear, ZIP→3 digits, Age capped 90. Scorecard → **PASS: 0/18 in Gold.** |
| **4 — Access & re-ID** | 5m | **Analyst**, then Admin | Analyst: open `gold_safe_*` report (full analytics, zero PHI); run `SELECT MRN …` → **denied**; show region RLS. Admin: show `NB_reidentify` in Vault only | *"Full analytics, no PHI. Try to read MRN — blocked by column security. Re-id is a break-glass exception: Vault workspace, ~2 people, HMAC-irreversible without the crosswalk, every use audited."* Don't dwell. |
| **5 — Why it matters** | 3m | Admin | Slide / summary | *"Gold is **not PHI** → safe for Copilot/AI without BAA-scoped limits. Everything ran **in-tenant, Microsoft-native** — Spark + Key Vault + OneLake security. Start at **Tier 0 today**; Tier 3 is the north-star it unlocks."* Note Tonic (Marketplace, MACC-eligible) for free-text/synthetic. |

---

## If something breaks (recovery lines)

- **Notebook stalls / Spark slow** → switch to the pre-run output tab: *"I ran this earlier so we don't watch Spark spin — here's the result."*
- **Label apply fails in Catalog** → *"I've pre-applied these; here's what the steward sees"* (backup screenshot / pre-labeled dataset).
- **Analyst denial doesn't fire** → fall back to showing the CLS policy in `sql/rls_cls_policies.sql` and the empty result set.
- **Wrong identity on screen** → just click the correct browser window; the color-coded profiles make it obvious.

## Top FAQ (have answers ready)

- **"Can an admin still see PHI in Gold?"** → There's no PHI in Gold to see — it was **removed, not hidden**. Raw PHI is isolated in the Raw workspace.
- **"Does the catalog expose our data tenant-wide?"** → No — discovery-only. See [tier0/eis_security_one_pager.md](../tier0/eis_security_one_pager.md).
- **"Is Fabric HIPAA compliant?"** → Shared responsibility; HIPAA-capable under the BAA. See [hipaa_compliance.md](hipaa_compliance.md).
- **"What about doctors' notes / free text?"** → Already wired. `FactEncounter.ReasonForVisitNote` runs through the `redact_text` strategy (`ner_text.py`) on every Silver run — Presidio when the `[nlp]` extra is installed, a regex detector otherwise. The scorecard publishes measured recall from a labeled fixture, so you can state the number rather than the intent. On the regex-only backend, structured identifiers (MRN, phone, email, SSN, card, IP, URL) are caught and contextual ones (names, places, dates) need the Presidio model — say that out loud; it is the honest answer.
