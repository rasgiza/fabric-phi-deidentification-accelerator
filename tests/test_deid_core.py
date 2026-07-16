"""Local unit tests for the pure-Python de-id core. Run: pytest -q

These verify the two properties that matter most for a de-id engine:
  1. Determinism / referential integrity — same input + same pepper => same token,
     so joins survive de-identification across tables.
  2. Correct Safe Harbor transforms — dates -> year, ZIP -> 3 digits (000 for low-pop),
     age capped at 90, unknown columns suppressed by default.
No PySpark required.
"""

import os

import pytest

from fabric_phi_deid.tokenization import (
    tokenize,
    tokenize_format_preserving,
    tokenize_numeric,
)

PEPPER = "unit-test-pepper-not-a-real-secret"


# ---------------------------------------------------------------------------
# tokenization
# ---------------------------------------------------------------------------
def test_tokenize_is_deterministic():
    assert tokenize("MRN12345", PEPPER, namespace="mrn") == tokenize(
        "MRN12345", PEPPER, namespace="mrn"
    )


def test_tokenize_preserves_referential_integrity_across_tables():
    # Same patient value tokenizes identically wherever it appears -> joins survive.
    patient_in_dim = tokenize("PATKEY-9", PEPPER, namespace="patient")
    patient_in_fact = tokenize("PATKEY-9", PEPPER, namespace="patient")
    assert patient_in_dim == patient_in_fact


def test_tokenize_namespace_prevents_cross_column_collision():
    # Same raw string under different namespaces must NOT collide.
    assert tokenize("123", PEPPER, namespace="mrn") != tokenize("123", PEPPER, namespace="npi")


def test_tokenize_changes_with_pepper():
    assert tokenize("MRN12345", PEPPER) != tokenize("MRN12345", "different-pepper")


def test_tokenize_passthrough_none_and_empty():
    assert tokenize(None, PEPPER) is None
    assert tokenize("", PEPPER) == ""


def test_tokenize_empty_pepper_raises():
    with pytest.raises(ValueError):
        tokenize("value", "")


def test_tokenize_prefix_and_length():
    tok = tokenize("MRN1", PEPPER, prefix="PT-", length=10)
    assert tok.startswith("PT-")
    assert len(tok) == len("PT-") + 10


def test_tokenize_numeric_shape():
    tok = tokenize_numeric("12345", PEPPER, digits=10)
    assert tok.isdigit() and len(tok) == 10
    assert tokenize_numeric("12345", PEPPER) == tokenize_numeric("12345", PEPPER)


def test_format_preserving_keeps_shape():
    tok = tokenize_format_preserving("A12-3456", PEPPER, namespace="mrn")
    assert len(tok) == len("A12-3456")
    assert tok[3] == "-"  # separator preserved in place
    assert tok[0].isupper()  # letter stays a letter
    assert tok[1].isdigit()  # digit stays a digit


# ---------------------------------------------------------------------------
# strategy engine
# ---------------------------------------------------------------------------
from fabric_phi_deid.deid_engine import (
    apply_strategy,
    load_rules,
    resolve_column_strategy,
)


def test_generalize_date_to_year():
    assert apply_strategy("1984-07-22", "generalize", {"kind": "year"}, PEPPER) == 1984


def test_generalize_zip3_truncates():
    assert apply_strategy("100211234", "generalize", {"kind": "zip3"}, PEPPER) == "100"


def test_generalize_zip3_zeroes_restricted_prefix():
    # 036 is a HIPAA low-population restricted prefix -> must become 000.
    assert apply_strategy("03612", "generalize", {"kind": "zip3"}, PEPPER) == "000"


def test_generalize_age_cap():
    assert apply_strategy(95, "generalize", {"kind": "age_cap", "cap": 90}, PEPPER) == 90
    assert apply_strategy(40, "generalize", {"kind": "age_cap", "cap": 90}, PEPPER) == 40


def test_suppress_returns_none_by_default():
    assert apply_strategy("anything", "suppress", {}, PEPPER) is None


def test_synthesize_is_consistent_and_not_original():
    a = apply_strategy("Smith, John", "synthesize", {"kind": "name"}, PEPPER)
    b = apply_strategy("Smith, John", "synthesize", {"kind": "name"}, PEPPER)
    assert a == b and a != "Smith, John"


def test_date_shift_is_per_entity_consistent_and_preserves_interval():
    import datetime as dt

    p = {"entity_value": "PATKEY-9", "max_days": 365}
    d1 = apply_strategy("2020-01-01", "date_shift", p, PEPPER)
    d2 = apply_strategy("2020-02-01", "date_shift", p, PEPPER)
    # Same entity shifts both dates by the same offset -> the 31-day gap is preserved.
    assert (d2 - d1) == dt.timedelta(days=31)


def test_date_shift_differs_across_entities():
    d_a = apply_strategy("2020-01-01", "date_shift", {"entity_value": "A"}, PEPPER)
    d_b = apply_strategy("2020-01-01", "date_shift", {"entity_value": "B"}, PEPPER)
    assert d_a != d_b


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        apply_strategy("x", "nope", {}, PEPPER)


# ---------------------------------------------------------------------------
# config resolution (uses the shipped deid_rules.yaml)
# ---------------------------------------------------------------------------
RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "deid_rules.yaml")


def _cfg():
    return load_rules(RULES_PATH)


def test_config_loads_and_has_profiles():
    cfg = _cfg()
    assert "safe_harbor" in cfg["profiles"]
    assert "expert_determination" in cfg["profiles"]


def test_default_strategy_is_deny_by_default():
    cfg = _cfg()
    strat, _ = resolve_column_strategy(
        cfg, "safe_harbor", "dim_patient", "SomeNewUnclassifiedColumn"
    )
    assert strat == "suppress"


def test_mrn_resolves_to_tokenize():
    cfg = _cfg()
    strat, params = resolve_column_strategy(cfg, "safe_harbor", "dim_patient", "MRN")
    assert strat == "tokenize" and params.get("namespace") == "mrn"


def test_dob_resolves_to_year_generalization():
    cfg = _cfg()
    strat, params = resolve_column_strategy(cfg, "safe_harbor", "dim_patient", "DateOfBirth")
    assert strat == "generalize" and params.get("kind") == "year"


def test_surrogate_key_passthrough():
    cfg = _cfg()
    strat, _ = resolve_column_strategy(cfg, "safe_harbor", "dim_patient", "PatientKey")
    assert strat == "passthrough"
