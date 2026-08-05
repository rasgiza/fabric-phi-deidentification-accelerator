"""Multi-source contract tests: two Epic-shaped schemas, one de-identification engine.

The accelerator ships two synthetic sources -- Caboodle (dimensional, ``dim_*``/``fact_*``)
and Clarity (normalized transactional, ``clarity_*``). Nothing in the notebooks or the
engine knows a table name; the rulebook is the only place the two schemas are described.
That makes the rulebook the single point of failure, so these tests guard the invariants
that would otherwise fail *silently* -- producing output that looks fine but is either
unusable (tokens don't line up) or unsafe (an identifier passed through).
"""

from __future__ import annotations

import pytest

from fabric_phi_deid.deid_engine import apply_strategy, resolve_column_strategy

CABOODLE_TABLES = (
    "dim_patient",
    "dim_provider",
    "dim_provider_credential",
    "dim_facility",
    "fact_claim",
    "fact_encounter",
    "fact_risk_score",
)

CLARITY_TABLES = (
    "clarity_patient",
    "clarity_pat_enc",
    "clarity_pat_enc_hsp",
    "clarity_pat_enc_dx",
    "clarity_order_med",
    "clarity_order_proc",
    "clarity_order_results",
    "clarity_ser",
)

PROFILES = ("safe_harbor", "expert_determination")


def _profile_tables(cfg: dict, profile: str) -> dict:
    return cfg["profiles"][profile]["tables"]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("table", CABOODLE_TABLES + CLARITY_TABLES)
def test_every_phi_table_is_ruled_in_every_profile(cfg, profile, table):
    """A PHI table missing from a profile is not a crash -- it is a silent full suppression.

    ``deidentify_table`` falls back to the profile default (``suppress``) for unknown
    tables, so an omitted table yields columns of nulls that no one notices until an
    analyst asks why the cohort is empty.
    """
    assert table in _profile_tables(cfg, profile)


def test_profiles_cover_identical_table_sets(cfg):
    """Profiles must differ in HOW they de-identify, never in WHAT they cover.

    Safe Harbor and Expert Determination are two determinations over the same data. If
    one profile grew a table the other lacks, switching profiles would quietly change the
    shape of the output rather than just its precision.
    """
    sets = {p: set(_profile_tables(cfg, p)) for p in PROFILES}
    assert sets["safe_harbor"] == sets["expert_determination"], {
        "only_in_safe_harbor": sorted(sets["safe_harbor"] - sets["expert_determination"]),
        "only_in_expert_determination": sorted(sets["expert_determination"] - sets["safe_harbor"]),
    }


@pytest.mark.parametrize("profile", PROFILES)
def test_mrn_tokens_match_across_source_schemas(cfg, pepper, profile):
    """The same MRN must produce the same token in Caboodle and in Clarity.

    This is the whole point of keyed tokenization over random surrogates: a patient seen
    in both systems must reconcile downstream. The two schemas spell the column
    differently (``MRN`` vs ``PAT_MRN_ID``), so only a shared ``namespace`` keeps them
    aligned -- and a one-word typo in the rulebook would break it invisibly.
    """
    mrn = "MRN2000000"
    cab_strategy, cab_params = resolve_column_strategy(cfg, profile, "dim_patient", "MRN")
    clr_strategy, clr_params = resolve_column_strategy(
        cfg, profile, "clarity_patient", "PAT_MRN_ID"
    )

    assert cab_strategy == clr_strategy == "tokenize"
    assert apply_strategy(mrn, cab_strategy, cab_params, pepper) == apply_strategy(
        mrn, clr_strategy, clr_params, pepper
    )


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("clarity_patient", "PAT_ID"),
        ("clarity_patient", "PAT_MRN_ID"),
        ("clarity_patient", "PAT_NAME"),
        ("clarity_patient", "PAT_FIRST_NAME"),
        ("clarity_patient", "PAT_LAST_NAME"),
        ("clarity_patient", "BIRTH_DATE"),
        ("clarity_patient", "DEATH_DATE"),
        ("clarity_patient", "HOME_PHONE"),
        ("clarity_patient", "EMAIL_ADDRESS"),
        ("clarity_patient", "ADD_LINE_1"),
        ("clarity_patient", "CITY"),
        ("clarity_patient", "ZIP"),
        ("clarity_pat_enc", "PAT_ENC_CSN_ID"),
        ("clarity_ser", "PROV_NAME"),
        ("clarity_ser", "NPI"),
    ],
)
def test_clarity_direct_identifiers_are_never_passed_through(cfg, profile, table, column):
    """Each of these is a HIPAA Safe Harbor identifier as it appears in Clarity.

    ``PAT_ENC_CSN_ID`` is included deliberately: unlike Caboodle's internal encounter
    surrogate, a CSN is printed on discharge paperwork and shown in the UI, so it is a
    real-world identifier rather than a warehouse artifact.
    """
    strategy, _ = resolve_column_strategy(cfg, profile, table, column)
    assert strategy != "passthrough", f"{profile}/{table}.{column} leaks a direct identifier"


@pytest.mark.parametrize("table", CLARITY_TABLES)
def test_safe_harbor_never_emits_a_full_date(cfg, table):
    """Safe Harbor permits year only -- (b)(2)(iii). Date shifting belongs to (b)(1).

    Scans by column NAME rather than by declared rule so a newly added ``*_DATE`` /
    ``*_TIME`` column cannot slip in with a permissive rule attached.
    """
    rules = _profile_tables(cfg, "safe_harbor")[table]
    offenders = []
    for column in rules:
        if not (column.endswith(("_DATE", "_TIME")) or column == "BIRTH_DATE"):
            continue
        strategy, params = resolve_column_strategy(cfg, "safe_harbor", table, column)
        if strategy == "suppress":
            continue
        if strategy == "generalize" and params.get("kind") in {"year", "birth_year"}:
            continue
        offenders.append((column, strategy, params))
    assert not offenders, f"{table}: Safe Harbor must reduce dates to a year -- {offenders}"


@pytest.mark.parametrize("table", CLARITY_TABLES)
def test_expert_determination_shifts_dates_by_patient(cfg, table):
    """Date shifting must be keyed on the PATIENT, not the row.

    A per-row offset would destroy the intervals (length of stay, time-to-treatment) that
    make Expert Determination worth the extra risk analysis in the first place. Every
    Clarity table carries ``PAT_ID``, so that is the correct entity key throughout.
    """
    rules = _profile_tables(cfg, "expert_determination")[table]
    for column in rules:
        strategy, params = resolve_column_strategy(cfg, "expert_determination", table, column)
        if strategy != "date_shift":
            continue
        assert params.get("entity_column") == "PAT_ID", (
            f"{table}.{column} shifts dates by {params.get('entity_column')!r}; "
            "shifting by anything other than the patient breaks interval integrity"
        )
