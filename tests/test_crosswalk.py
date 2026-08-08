"""Tests for randomly assigned re-identification codes (§164.514(c)).

The whole value of ``surrogate`` over ``tokenize`` rests on one property: the code must
NOT be a function of the identifier. That is invisible in normal use -- both produce a
stable-looking key -- so it is asserted here directly, along with the failure modes that
would silently merge or split patients.
"""

from __future__ import annotations

import pytest

from fabric_phi_deid.crosswalk import (
    CROSSWALK_SOURCE_COLUMN,
    SURROGATE_COLUMN,
    SURROGATE_PREFIX,
    crosswalk_from_rows,
    crosswalk_to_rows,
    mint_crosswalk,
    mint_surrogate_id,
)
from fabric_phi_deid.deid_engine import apply_strategy
from fabric_phi_deid.determination import (
    DERIVED_VALUE_STRATEGIES,
    SAFE_HARBOR,
    assess_method_eligibility,
)

PEPPER = "unit-test-pepper-not-a-secret"


# ---------------------------------------------------------------------------------------
# The §164.514(c)(1) property
# ---------------------------------------------------------------------------------------
def test_the_same_identifier_minted_twice_gets_two_different_codes():
    """The defining difference from ``tokenize``.

    If this ever passes by returning equal codes, the code has become a function of the
    identifier and the Safe Harbor argument for this strategy is void.
    """
    first = mint_crosswalk(["MRN00000102"])
    second = mint_crosswalk(["MRN00000102"])
    assert first["MRN00000102"] != second["MRN00000102"]


def test_a_code_is_unpredictable_across_draws():
    assert len({mint_surrogate_id() for _ in range(200)}) == 200


def test_codes_carry_the_prefix_and_no_trace_of_the_input():
    code = mint_crosswalk(["MRN00000102"])["MRN00000102"]
    assert code.startswith(SURROGATE_PREFIX)
    assert "MRN" not in code


def test_surrogate_is_not_counted_as_a_derived_value():
    """Guards the reasoning, not just the constant.

    ``surrogate`` must stay off the derived list or Safe Harbor loses its only lawful way
    to link across source systems.
    """
    assert "surrogate" not in DERIVED_VALUE_STRATEGIES


def test_a_profile_that_surrogate_keys_may_still_claim_safe_harbor():
    cfg = {
        "profiles": {
            "sh": {
                "default_strategy": "suppress",
                "tables": {
                    "dim_patient": {
                        "MRN": {"strategy": "surrogate"},
                        "PatientName": "suppress",
                        "ZIP": {"strategy": "generalize", "kind": "zip3"},
                    }
                },
            }
        }
    }
    result = assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR, profile="sh")
    assert result.passes, result.summary()
    assert result.claimable_method == SAFE_HARBOR


def test_the_same_profile_with_tokenize_instead_may_not():
    """The A/B that proves the previous test is measuring the strategy, not the shape."""
    cfg = {
        "profiles": {
            "sh": {
                "default_strategy": "suppress",
                "tables": {"dim_patient": {"MRN": {"strategy": "tokenize"}}},
            }
        }
    }
    result = assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR, profile="sh")
    assert not result.passes
    assert [r.strategy for r in result.derived_rules] == ["tokenize"]


# ---------------------------------------------------------------------------------------
# Stability across runs -- the operational half
# ---------------------------------------------------------------------------------------
def test_rerunning_with_the_existing_mapping_does_not_renumber_anybody():
    first = mint_crosswalk(["A", "B"])
    second = mint_crosswalk(["A", "B", "C"], existing=first)
    assert second["A"] == first["A"]
    assert second["B"] == first["B"]
    assert "C" in second


def test_a_reissued_code_can_never_collide_with_a_live_one():
    existing = mint_crosswalk([str(i) for i in range(50)])
    grown = mint_crosswalk([str(i) for i in range(100)], existing=existing)
    assert len(set(grown.values())) == len(grown)


def test_blank_and_missing_identifiers_are_skipped_rather_than_keyed():
    mapping = mint_crosswalk(["MRN1", "", "   ", None, "MRN1"])  # type: ignore[list-item]
    assert set(mapping) == {"MRN1"}


def test_identifiers_are_matched_after_trimming_whitespace():
    mapping = mint_crosswalk(["MRN1"])
    assert apply_strategy(" MRN1 ", "surrogate", {"crosswalk": mapping}, PEPPER) == mapping["MRN1"]


# ---------------------------------------------------------------------------------------
# Conformance across source systems
# ---------------------------------------------------------------------------------------
def test_two_source_systems_holding_the_same_mrn_land_on_one_code():
    caboodle_mrns = ["MRN00000102", "MRN00000103"]
    clarity_mrns = ["MRN00000102", "MRN00000900"]
    mapping = mint_crosswalk([*caboodle_mrns, *clarity_mrns])

    from_caboodle = apply_strategy("MRN00000102", "surrogate", {"crosswalk": mapping}, PEPPER)
    from_clarity = apply_strategy("MRN00000102", "surrogate", {"crosswalk": mapping}, PEPPER)
    assert from_caboodle == from_clarity
    assert len(mapping) == 3


# ---------------------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------------------
def test_an_unmapped_identifier_raises_instead_of_minting_a_second_code():
    """A per-row mint would split one patient into many on a task retry."""
    with pytest.raises(KeyError, match="No assigned code"):
        apply_strategy("MRN_NEVER_SEEN", "surrogate", {"crosswalk": {"MRN1": "DEID-x"}}, PEPPER)


def test_a_missing_crosswalk_raises_rather_than_passing_the_identifier_through():
    with pytest.raises(ValueError, match="requires 'crosswalk'"):
        apply_strategy("MRN00000102", "surrogate", {}, PEPPER)


def test_nulls_and_blanks_survive_as_themselves():
    assert apply_strategy(None, "surrogate", {"crosswalk": {}}, PEPPER) is None
    assert apply_strategy("", "surrogate", {"crosswalk": {}}, PEPPER) == ""


# ---------------------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------------------
def test_the_mapping_survives_a_round_trip_through_vault_rows():
    mapping = mint_crosswalk(["MRN1", "MRN2", "MRN3"])
    assert crosswalk_from_rows(crosswalk_to_rows(mapping)) == mapping


def test_one_identifier_with_two_codes_is_rejected_as_corrupt():
    rows = [
        {CROSSWALK_SOURCE_COLUMN: "MRN1", SURROGATE_COLUMN: "DEID-aaaa"},
        {CROSSWALK_SOURCE_COLUMN: "MRN1", SURROGATE_COLUMN: "DEID-bbbb"},
    ]
    with pytest.raises(ValueError, match="maps to both"):
        crosswalk_from_rows(rows)


def test_one_code_shared_by_two_identifiers_is_rejected_as_corrupt():
    """The dangerous direction: two people silently conformed onto one key."""
    rows = [
        {CROSSWALK_SOURCE_COLUMN: "MRN1", SURROGATE_COLUMN: "DEID-aaaa"},
        {CROSSWALK_SOURCE_COLUMN: "MRN2", SURROGATE_COLUMN: "DEID-aaaa"},
    ]
    with pytest.raises(ValueError, match="assigned to both"):
        crosswalk_from_rows(rows)


def test_an_exact_duplicate_row_is_tolerated():
    rows = [
        {CROSSWALK_SOURCE_COLUMN: "MRN1", SURROGATE_COLUMN: "DEID-aaaa"},
        {CROSSWALK_SOURCE_COLUMN: "MRN1", SURROGATE_COLUMN: "DEID-aaaa"},
    ]
    assert crosswalk_from_rows(rows) == {"MRN1": "DEID-aaaa"}
