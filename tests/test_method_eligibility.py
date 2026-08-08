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
                        "PatientName": "suppress",
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


def test_synthesize_blocks_a_safe_harbor_claim():
    """A fake name is still derived when the generator is seeded with the real one.

    ``strat_synthesize`` HMACs the source value and indexes a name list with the digest, so
    the same real name always yields the same fake one -- a consistent, pepper-keyed pseudonym.
    Small output space or not, HHS §3.2 rules out even patient initials, so a derived remnant
    of a name does not survive a Safe Harbor claim.
    """
    cfg = {
        "active_profile": "p",
        "profiles": {"p": {"tables": {"dim_patient": {"PatientName": {"strategy": "synthesize"}}}}},
    }
    verdict = assess_method_eligibility(cfg, claimed_method=SAFE_HARBOR)
    assert not verdict.passes
    assert verdict.derived_rules[0].strategy == "synthesize"


def test_redact_text_is_judged_on_its_replacement_parameter():
    """The strategy name alone cannot answer this one, so the checker must read the params.

    ``replacement: label`` swaps each detected span for its entity type and is a removal.
    ``replacement: token`` swaps it for a value derived from the span, which is a pseudonym
    living inside free text -- the easiest place for one to hide from a column-level review.
    """

    def _cfg(replacement):
        return {
            "active_profile": "p",
            "profiles": {
                "p": {
                    "tables": {
                        "fact_encounter": {
                            "Note": {"strategy": "redact_text", "replacement": replacement}
                        }
                    }
                }
            },
        }

    assert assess_method_eligibility(_cfg("label"), claimed_method=SAFE_HARBOR).passes
    verdict = assess_method_eligibility(_cfg("token"), claimed_method=SAFE_HARBOR)
    assert not verdict.passes
    assert verdict.derived_rules[0].strategy == "redact_text(replacement=token)"


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


# --------------------------------------------------------------------------------------
# The shipped safe_harbor_strict profile — the one that may actually make the claim
#
# These are the tests that turn the profile's promise into something mechanical. The
# comment block in deid_rules.yaml explains *why* each rule is what it is; these assert
# that it still is. Anyone who later adds a tokenize rule "just to keep the join" fails
# here rather than in front of a compliance reviewer.
# --------------------------------------------------------------------------------------
SAFE_HARBOR_STRICT = "safe_harbor_strict"


def test_safe_harbor_strict_may_claim_safe_harbor(shipped_config):
    verdict = assess_method_eligibility(
        shipped_config, claimed_method=SAFE_HARBOR, profile=SAFE_HARBOR_STRICT
    )
    assert verdict.passes, verdict.summary()
    assert verdict.safe_harbor_available
    assert verdict.claimable_method == SAFE_HARBOR
    assert verdict.derived_rules == ()


def test_safe_harbor_strict_covers_both_source_systems(shipped_config):
    """Multi-source Safe Harbor is the whole point, and it is easy to lose by accident.

    Cross-source linkage cannot use a key *derived* from the MRN. For a long time that was
    read as "so Safe Harbor is Caboodle-only" -- which is wrong: §164.514(c) permits an
    *assigned* code. If a Clarity table disappears from this profile, someone has quietly
    given up on conforming the second source rather than fixing the key.
    """
    tables = shipped_config["profiles"][SAFE_HARBOR_STRICT]["tables"]
    assert [t for t in tables if t.startswith("clarity_")]
    assert "clarity_patient" in tables


def test_safe_harbor_strict_denies_by_default(shipped_config):
    """An unlisted column must be dropped, not passed through."""
    assert shipped_config["profiles"][SAFE_HARBOR_STRICT]["default_strategy"] == "suppress"


def test_safe_harbor_strict_assigns_the_mrn_code_rather_than_deriving_it(shipped_config):
    """§164.514(c)(1) admits a code only when it is not derived from the individual.

    ``surrogate`` draws from a CSPRNG, so the code carries no information about the patient
    and the mapping exists only in the Vault. ``tokenize`` would also join, and would also
    look like an opaque string -- and would forfeit the claim, because it is reproducible
    from the MRN. The two are one word apart in YAML, which is exactly why this is a test.
    """
    tables = shipped_config["profiles"][SAFE_HARBOR_STRICT]["tables"]
    assert tables["dim_patient"]["MRN"] == {"strategy": "surrogate"}
    assert tables["clarity_patient"]["PAT_MRN_ID"] == {"strategy": "surrogate"}


def test_safe_harbor_strict_caps_age_and_truncates_zip(shipped_config):
    """The two generalizations Safe Harbor names explicitly: 90+ aggregation and ZIP3."""
    patient = shipped_config["profiles"][SAFE_HARBOR_STRICT]["tables"]["dim_patient"]
    assert patient["Age"] == {"strategy": "generalize", "kind": "age_cap", "cap": 90}
    assert patient["ZIP"] == {"strategy": "generalize", "kind": "zip3"}
    assert patient["DateOfBirth"] == {
        "strategy": "generalize",
        "kind": "birth_year",
        "cap_age": 90,
    }


def test_safe_harbor_strict_keeps_no_free_text(shipped_config):
    """§3.10: identifiers must go regardless of location. This profile removes rather than
    detects, so no narrative column may survive under any strategy."""
    tables = shipped_config["profiles"][SAFE_HARBOR_STRICT]["tables"]
    assert tables["fact_encounter"]["ReasonForVisitNote"] == "suppress"
    strategies = {
        rule if isinstance(rule, str) else rule["strategy"]
        for columns in tables.values()
        for rule in columns.values()
    }
    assert "redact_text" not in strategies


def test_safe_harbor_strict_suppresses_names_rather_than_synthesizing_them(shipped_config):
    """Synthesized names are HMAC-seeded by the real name, so they are derived values.

    This is the rule that is easiest to relax by accident -- a NULL name column looks like a
    bug and `synthesize` looks like the obvious fix. It is not one, for this profile.
    """
    tables = shipped_config["profiles"][SAFE_HARBOR_STRICT]["tables"]
    strategies = {
        rule if isinstance(rule, str) else rule["strategy"]
        for columns in tables.values()
        for rule in columns.values()
    }
    assert "synthesize" not in strategies
    assert tables["dim_patient"]["PatientName"] == "suppress"


def test_the_misnamed_safe_harbor_profile_still_cannot_claim_it(shipped_config):
    """Guards the distinction the new profile exists to make.

    `safe_harbor` is named for its column treatments and emits MRN-derived tokens, so the
    claimable method there is Expert Determination. If this ever starts passing, the two
    profiles have collapsed into one and the naming is no longer merely misleading.
    """
    verdict = assess_method_eligibility(
        shipped_config, claimed_method=SAFE_HARBOR, profile="safe_harbor"
    )
    assert not verdict.passes
    assert verdict.claimable_method == EXPERT_DETERMINATION


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
