"""Contract tests for the gold-layer conformance spec.

``gold_conform`` is the one place where the two Epic-shaped schemas stop being handled
generically and start being *modelled*. Everything upstream of it is table-name-agnostic;
everything downstream (Power BI, Copilot) trusts it blindly. That makes it exactly the
kind of code where a mistake is invisible until it is embarrassing -- a column of nulls
that looks like missing data, or worse, an identifier projected into the published star.

These tests run without Spark or a Fabric runtime, so CI can prove the star's shape is
consistent with ``config/deid_rules.yaml`` on every commit.
"""

from __future__ import annotations

import pytest

from fabric_phi_deid import gold_conform as gc
from fabric_phi_deid.deid_engine import apply_strategy, resolve_column_strategy

PROFILES = ("safe_harbor", "expert_determination")


def _profile_tables(cfg: dict, profile: str) -> dict:
    return cfg["profiles"][profile]["tables"]


def _projections(cfg: dict, profile: str):
    return list(gc.iter_ruled_projections(_profile_tables(cfg, profile)))


# ---------------------------------------------------------------------------
# The star may only publish columns the rulebook actually governs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_every_projected_column_is_explicitly_ruled(cfg, profile):
    """Gold must never project a column the rulebook has not seen.

    ``deidentify_table`` falls back to the profile default for unknown columns. That
    default is ``suppress``, so an unruled column does not crash -- it publishes nulls.
    Pinning the projection here and asserting it against the rulebook turns that silent
    data-quality bug into a failing build.
    """
    tables = _profile_tables(cfg, profile)
    missing = [
        f"{gold}: {silver}.{column}"
        for gold, silver, column in _projections(cfg, profile)
        if column not in tables[silver]
    ]
    assert not missing, f"gold projects columns with no rule in {profile}: {missing}"


@pytest.mark.parametrize("profile", PROFILES)
def test_no_projected_column_is_suppressed(cfg, profile):
    """A suppressed column is a column of nulls -- publishing it is always a mistake.

    This is the test that keeps the two halves honest: if someone tightens a rule to
    ``suppress`` (say ``CITY``), the gold spec must drop that column in the same commit
    rather than shipping an empty field to analysts.
    """
    offenders = []
    for gold, silver, column in _projections(cfg, profile):
        strategy, _ = resolve_column_strategy(cfg, profile, silver, column)
        if strategy == "suppress":
            offenders.append(f"{gold}: {silver}.{column}")
    assert not offenders, f"gold projects suppressed columns in {profile}: {offenders}"


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize(
    "column", ["HOME_PHONE", "EMAIL_ADDRESS", "ADD_LINE_1", "CITY", "PAT_FIRST_NAME", "PAT_LAST_NAME"]
)
def test_clarity_direct_identifiers_never_reach_the_patient_dimension(cfg, profile, column):
    """Belt-and-braces: these must be absent from the projection, not merely suppressed.

    ``test_no_projected_column_is_suppressed`` would already catch these, but naming them
    explicitly documents *why* the conformed patient dimension is narrower than the
    Clarity source table.
    """
    assert column not in gc.PATIENT_FROM_CLARITY.projected_columns


# ---------------------------------------------------------------------------
# Conformance: the two schemas must actually join
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_mrn_columns_share_one_token_namespace(cfg, profile):
    """The conformed patient dimension is only conformed if both MRNs tokenize identically.

    Same namespace *and* same prefix. If either drifts, the union below silently produces
    two rows per patient instead of one, and every cross-source cohort count is wrong.
    """
    cab_strategy, cab_params = resolve_column_strategy(
        cfg, profile, "dim_patient", gc.CABOODLE_MRN_COLUMN
    )
    clr_strategy, clr_params = resolve_column_strategy(
        cfg, profile, "clarity_patient", gc.CLARITY_MRN_COLUMN
    )
    assert cab_strategy == clr_strategy == "tokenize"
    assert cab_params.get("namespace") == clr_params.get("namespace") == gc.SHARED_MRN_NAMESPACE
    assert cab_params.get("prefix") == clr_params.get("prefix")


@pytest.mark.parametrize("profile", PROFILES)
def test_same_mrn_yields_one_conformed_patient_key(cfg, pepper, profile):
    """End-to-end proof of the join, not just of the config.

    A patient in both systems must land on a single ``dim_patient`` row. Since the
    Clarity-only key is minted from the MRN token, an identical MRN has to produce an
    identical token on both sides -- otherwise the "Both" case never matches and the
    patient forks.
    """
    mrn = "MRN0000451"
    _, cab_params = resolve_column_strategy(cfg, profile, "dim_patient", gc.CABOODLE_MRN_COLUMN)
    _, clr_params = resolve_column_strategy(
        cfg, profile, "clarity_patient", gc.CLARITY_MRN_COLUMN
    )
    caboodle_token = apply_strategy(mrn, "tokenize", cab_params, pepper=pepper)
    clarity_token = apply_strategy(mrn, "tokenize", clr_params, pepper=pepper)
    assert caboodle_token == clarity_token
    assert caboodle_token != mrn, "the MRN must not survive tokenization unchanged"


@pytest.mark.parametrize("profile", PROFILES)
def test_clarity_facts_link_through_a_consistently_tokenized_patient_id(cfg, profile):
    """Every Clarity fact must resolve to the patient dimension the same way.

    ``PAT_ID`` is tokenized, so the fact-side value only matches ``clarity_patient`` if
    both use one namespace. A fact that used a different namespace would join to nothing
    and quietly publish orphaned rows with a null ``PatientKey``.
    """
    _, patient_params = resolve_column_strategy(
        cfg, profile, "clarity_patient", gc.CLARITY_PATIENT_LINK
    )
    for name, spec in gc.CLARITY_GOLD.items():
        if spec.patient_link is None:
            continue
        assert spec.patient_link in spec.projected_columns, f"{name} declares a link it never projects"
        strategy, params = resolve_column_strategy(cfg, profile, spec.source, spec.patient_link)
        assert strategy == "tokenize", f"{name}.{spec.patient_link} must be tokenized"
        assert params.get("namespace") == patient_params.get("namespace"), (
            f"{name}.{spec.patient_link} uses a different namespace than clarity_patient"
        )


def test_every_clarity_fact_declares_a_patient_link():
    """A Clarity fact with no patient link cannot be filtered by cohort -- or by RLS.

    Provider is a dimension and is exempt; everything else is patient-grain.
    """
    unlinked = [
        name
        for name, spec in gc.CLARITY_GOLD.items()
        if spec.patient_link is None and not name.startswith("dim_")
    ]
    assert not unlinked, f"Clarity facts missing a patient link: {unlinked}"


# ---------------------------------------------------------------------------
# The conformed patient dimension must have exactly one schema
# ---------------------------------------------------------------------------


def test_both_sources_fill_the_same_patient_columns():
    """A union with mismatched columns is a runtime error in Spark and a silent
    schema-drift bug everywhere else. Assert both contributors cover the agreed output."""
    from_clarity = (
        set(gc.PATIENT_CLARITY_RENAMES)
        | set(gc.PATIENT_CLARITY_NULLS)
        | {"PatientKey", "ClarityPatientID", "BirthYear", gc.SOURCE_SYSTEM_COLUMN}
    )
    from_caboodle = (
        set(gc.PATIENT_FROM_CABOODLE.columns)
        | set(gc.PATIENT_FROM_CABOODLE.year_columns.values())
        | {"ClarityPatientID", gc.SOURCE_SYSTEM_COLUMN}
    )
    assert from_clarity == set(gc.PATIENT_GOLD_COLUMNS)
    assert from_caboodle == set(gc.PATIENT_GOLD_COLUMNS)


def test_clarity_patient_renames_reference_real_projected_columns():
    """Guards against a rename pointing at a column the projection does not read."""
    projected = set(gc.PATIENT_FROM_CLARITY.projected_columns)
    dangling = {g: c for g, c in gc.PATIENT_CLARITY_RENAMES.items() if c not in projected}
    assert not dangling, f"renames reference unprojected Clarity columns: {dangling}"


def test_clarity_patient_key_is_minted_from_the_shared_mrn_token():
    """Minting from anything else (e.g. PAT_ID) would give the same human two keys the
    moment they also appear in Caboodle."""
    assert gc.PATIENT_KEY_SOURCE_COLUMN == gc.CLARITY_MRN_COLUMN
    assert gc.PATIENT_KEY_SOURCE_COLUMN in gc.PATIENT_FROM_CLARITY.projected_columns


# ---------------------------------------------------------------------------
# Publish-gate wiring
# ---------------------------------------------------------------------------


def test_gold_table_list_is_unique_and_complete():
    """The publish gate iterates GOLD_TABLES. A missing entry is an unscanned table."""
    assert len(gc.GOLD_TABLES) == len(set(gc.GOLD_TABLES)), "duplicate gold table names"
    assert set(gc.GOLD_TABLES) == {"dim_patient", *gc.CABOODLE_GOLD, *gc.CLARITY_GOLD}


def test_clarity_is_optional_but_caboodle_is_not():
    """A Caboodle-only deployment must still build a valid star."""
    caboodle_only = gc.silver_dependencies(include_clarity=False)
    full = gc.silver_dependencies(include_clarity=True)
    assert "dim_patient" in caboodle_only
    assert not any(t.startswith("clarity_") for t in caboodle_only)
    assert set(caboodle_only).issubset(set(full))
    assert {t.source for t in gc.CLARITY_GOLD.values()}.issubset(set(full))


def test_silver_dependencies_are_deduplicated():
    deps = gc.silver_dependencies()
    assert len(deps) == len(set(deps))
