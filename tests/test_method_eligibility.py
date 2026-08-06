"""Tests for the HIPAA method-eligibility gate and the Safe Harbor birth-year floor.

These cover the two defects that made the compliance claim wrong rather than weak:

1. The pipeline emitted HMAC tokens derived from the MRN and still reported a Safe Harbor
   pass. Safe Harbor is a *removal* standard; §164.514(c)(1) admits a re-identification code
   only when it is "not derived from or related to information about the individual", and
   HHS §2.9 scopes hash-derived values to Expert Determination. The output was fine; the
   claim was not.

2. ``Age`` was capped at 90 while a true ``BirthYear`` was published alongside it, so the
   year reconstructed the age the cap had just removed. Safe Harbor requires removing ages
   over 89 *and* "all elements of dates (including year) indicative of such age".
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fabric_phi_deid.deid_engine import apply_strategy
from fabric_phi_deid.determination import (
    EXPERT_DETERMINATION,
    SAFE_HARBOR,
    ResidualScanResult,
    assess_method_eligibility,
    build_determination_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = REPO_ROOT / "config" / "deid_rules.yaml"


@pytest.fixture(scope="module")
def shipped_config() -> dict:
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------
# Method eligibility
# --------------------------------------------------------------------------------------
def test_tokenize_blocks_a_safe_harbor_claim():
    cfg = {
        "active_profile": "p",
        "profiles": {
            "p": {
                "tables": {
                    "dim_patient": {
                        "MRN": {"strategy": "tokenize", "namespace": "mrn"},
                        "ZIP": {"strategy": "generalize", "kind": "zip3"},
                    }
                }
            }
        },
    }
    verdict = assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR)
    assert not verdict.passes
    assert not verdict.safe_harbor_available
    assert verdict.claimable_method == EXPERT_DETERMINATION
    assert [str(r) for r in verdict.derived_rules] == ["dim_patient.MRN (tokenize)"]
    assert "Expert Determination" in verdict.summary()


def test_date_shift_blocks_a_safe_harbor_claim():
    """A per-entity shifted date is still a date derived from the patient's real dates."""
    cfg = {
        "active_profile": "p",
        "profiles": {
            "p": {
                "tables": {
                    "dim_patient": {
                        "DateOfBirth": {"strategy": "date_shift", "entity_column": "PatientKey"}
                    }
                }
            }
        },
    }
    verdict = assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR)
    assert not verdict.passes
    assert verdict.derived_rules[0].strategy == "date_shift"


def test_the_same_config_may_claim_expert_determination():
    """Derived values are permitted under §2.9 — the gate blocks the claim, not the data."""
    cfg = {
        "active_profile": "p",
        "profiles": {"p": {"tables": {"dim_patient": {"MRN": {"strategy": "tokenize"}}}}},
    }
    assert assess_method_eligibility(cfg, claimed_method=EXPERT_DETERMINATION).passes


def test_a_removal_only_profile_may_claim_safe_harbor():
    cfg = {
        "active_profile": "p",
        "profiles": {
            "p": {
                "tables": {
                    "dim_patient": {
                        "MRN": "suppress",
                        "PatientName": {"strategy": "synthesize", "kind": "name"},
                        "DateOfBirth": {"strategy": "generalize", "kind": "birth_year"},
                    }
                }
            }
        },
    }
    verdict = assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR)
    assert verdict.passes
    assert verdict.safe_harbor_available
    assert verdict.claimable_method == SAFE_HARBOR


def test_claiming_the_weaker_method_is_always_allowed():
    """Expert Determination over a Safe Harbor-clean config is a downgrade, not a violation."""
    cfg = {"active_profile": "p", "profiles": {"p": {"tables": {"t": {"c": "suppress"}}}}}
    assert assess_method_eligibility(cfg, claimed_method=EXPERT_DETERMINATION).passes


def test_shorthand_string_rules_are_inspected():
    """A bare `column: tokenize` must count the same as the dict form."""
    cfg = {"active_profile": "p", "profiles": {"p": {"tables": {"t": {"MRN": "tokenize"}}}}}
    assert not assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR).passes


def test_profile_defaults_to_active_profile(shipped_config):
    verdict = assess_method_eligibility(shipped_config, claimed_method=SAFE_HARBOR)
    assert verdict.profile == shipped_config["active_profile"]


def test_unknown_profile_yields_no_rules_rather_than_raising():
    cfg = {"active_profile": "p", "profiles": {"p": {"tables": {}}}}
    verdict = assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR, profile="nope")
    assert verdict.derived_rules == ()
    assert verdict.passes


# --------------------------------------------------------------------------------------
# The gate is wired into the evidence pack
# --------------------------------------------------------------------------------------
def _report(**kwargs):
    return build_determination_report(
        config_sha256="abc123",
        engine_version="0.1.0",
        residual_scan=ResidualScanResult(tables_scanned=1, rows_scanned=10),
        **kwargs,
    )


def test_determination_report_fails_on_an_unavailable_claim():
    cfg = {"active_profile": "p", "profiles": {"p": {"tables": {"t": {"MRN": "tokenize"}}}}}
    report = _report(
        method=SAFE_HARBOR,
        method_eligibility=assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR),
    )
    assert not report.passes
    assert report.to_dict()["method_eligibility"]["claimable_method"] == EXPERT_DETERMINATION
    assert "method-eligibility FAIL" in report.to_markdown()


def test_determination_report_passes_when_the_claim_matches():
    cfg = {"active_profile": "p", "profiles": {"p": {"tables": {"t": {"MRN": "tokenize"}}}}}
    report = _report(
        method=EXPERT_DETERMINATION,
        method_eligibility=assess_method_eligibility(cfg, claimed_method=EXPERT_DETERMINATION),
    )
    assert report.passes


def test_eligibility_is_optional_and_absent_does_not_block():
    """Back-compat: existing callers that supply no eligibility still gate on the old checks."""
    report = _report(method=SAFE_HARBOR)
    assert report.passes
    assert report.to_dict()["method_eligibility"] is None


# --------------------------------------------------------------------------------------
# The shipped rulebook
# --------------------------------------------------------------------------------------
def test_shipped_safe_harbor_profile_cannot_claim_safe_harbor(shipped_config):
    """Documents reality: the shipped profile tokenizes, so the claim must be the weaker one.

    If someone later removes every tokenize/date_shift rule this test should be updated, not
    deleted — that would be a genuine posture change worth noticing.
    """
    verdict = assess_method_eligibility(shipped_config, claimed_method=SAFE_HARBOR)
    assert not verdict.passes
    assert verdict.claimable_method == EXPERT_DETERMINATION
    tokenized = {r.column for r in verdict.derived_rules if r.strategy == "tokenize"}
    assert {"MRN", "PAT_MRN_ID", "PAT_ID"} <= tokenized


def test_shipped_expert_determination_profile_also_derives(shipped_config):
    verdict = assess_method_eligibility(
        shipped_config, claimed_method=EXPERT_DETERMINATION, profile="expert_determination"
    )
    assert verdict.passes
    assert not verdict.safe_harbor_available


# --------------------------------------------------------------------------------------
# Safe Harbor birth-year floor
# --------------------------------------------------------------------------------------
def _birth_year(value: str, **params):
    return apply_strategy(value, "generalize", {"kind": "birth_year", **params}, pepper="p")


def test_birth_year_passes_through_when_age_is_under_the_cap():
    assert _birth_year("1975-06-23", reference_year=2026) == 1975


def test_birth_year_is_floored_when_it_would_imply_age_over_89():
    """HHS's own worked example: born 1910, seen 2010 -> report 'on or before 1920'."""
    assert _birth_year("1910-04-02", reference_year=2010) == 1920


def test_every_90_plus_patient_lands_in_one_bucket():
    """The floor must aggregate, not merely shift — otherwise it still discriminates ages."""
    years = {_birth_year(f"{y}-01-01", reference_year=2026) for y in (1900, 1910, 1925, 1930)}
    assert years == {1936}


def test_the_boundary_year_is_not_floored():
    """Exactly at the cap the patient is 90 by year arithmetic; one year later they are 89."""
    assert _birth_year("1937-01-01", reference_year=2026) == 1937


def test_birth_year_defaults_to_a_cap_of_90():
    assert _birth_year("1800-01-01", reference_year=2026) == 1936


def test_birth_year_honours_a_custom_cap():
    assert _birth_year("1800-01-01", reference_year=2026, cap_age=100) == 1926


def test_birth_year_handles_empty_and_null():
    assert _birth_year("") == ""
    assert apply_strategy(None, "generalize", {"kind": "birth_year"}, pepper="p") is None


def test_birth_year_returns_none_for_unparseable_input():
    assert _birth_year("not-a-date", reference_year=2026) is None


def test_birth_year_without_a_reference_year_uses_the_current_year():
    """Unpinned it must still floor; the bucket just moves with time."""
    from datetime import UTC, datetime

    assert _birth_year("1800-01-01") == datetime.now(UTC).year - 90


def test_shipped_config_uses_birth_year_for_both_schemas(shipped_config):
    tables = shipped_config["profiles"][SAFE_HARBOR]["tables"]
    assert tables["dim_patient"]["DateOfBirth"]["kind"] == "birth_year"
    assert tables["clarity_patient"]["BIRTH_DATE"]["kind"] == "birth_year"


def test_plain_year_still_available_for_non_birth_dates(shipped_config):
    """Service/encounter dates are correctly year-only; the floor must not have leaked to them."""
    tables = shipped_config["profiles"][SAFE_HARBOR]["tables"]
    assert tables["fact_encounter"]["EncounterDate"]["kind"] == "year"
