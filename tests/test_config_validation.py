"""Tests for config schema validation + coverage linting, and drift protection."""

from __future__ import annotations

import pytest

from fabric_phi_deid.config import (
    STRATEGY_SPECS,
    ConfigValidationError,
    audit_coverage,
    validate_config,
)
from fabric_phi_deid.deid_engine import STRATEGIES, load_rules


def test_shipped_config_is_valid(cfg):
    assert validate_config(cfg) == []


def test_strategy_specs_match_engine_registry():
    # Drift guard: config's declared strategies must equal the engine's real registry.
    assert set(STRATEGY_SPECS) == set(STRATEGIES)


def test_unknown_strategy_is_rejected():
    bad = {
        "profiles": {
            "p": {"default_strategy": "suppress", "tables": {"t": {"col": {"strategy": "encrypt"}}}}
        }
    }
    errors = validate_config(bad)
    assert any("unknown strategy" in e for e in errors)


def test_generalize_requires_valid_kind():
    bad = {"profiles": {"p": {"tables": {"t": {"c": {"strategy": "generalize", "kind": "month"}}}}}}
    errors = validate_config(bad)
    assert any("kind" in e for e in errors)


def test_date_shift_requires_entity_column():
    bad = {"profiles": {"p": {"tables": {"t": {"c": {"strategy": "date_shift"}}}}}}
    errors = validate_config(bad)
    assert any("entity_column" in e for e in errors)


def test_active_profile_must_exist():
    bad = {"active_profile": "ghost", "profiles": {"real": {"tables": {}}}}
    errors = validate_config(bad)
    assert any("active_profile" in e for e in errors)


def test_missing_profiles_is_rejected():
    assert validate_config({}) == ["Config must define a non-empty 'profiles' mapping."]


def test_dict_rule_without_strategy_is_rejected():
    bad = {"profiles": {"p": {"tables": {"t": {"c": {"namespace": "mrn"}}}}}}
    errors = validate_config(bad)
    assert any("missing required 'strategy'" in e for e in errors)


def test_load_rules_raises_on_invalid(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("profiles:\n  p:\n    tables:\n      t:\n        c: {strategy: nope}\n")
    with pytest.raises(ConfigValidationError):
        load_rules(str(p))


# --- coverage linter -------------------------------------------------------------------
def test_coverage_flags_defaulted_and_missing(cfg):
    # 'MRN' is classified; 'MysteryCol' is not (-> defaulted); 'FirstName' present.
    # 'GhostColumn' is in neither the data nor rules; a ruled-but-absent column is 'missing'.
    columns = ["MRN", "FirstName", "MysteryCol"]  # omits many ruled cols -> they're 'missing'
    report = audit_coverage(cfg, "safe_harbor", "dim_patient", columns)
    assert "MRN" in report.classified
    assert "MysteryCol" in report.defaulted
    assert "PatientKey" in report.missing  # ruled in config but absent from these columns
    assert report.default_strategy == "suppress"


def test_coverage_ignores_lineage_columns(cfg):
    report = audit_coverage(cfg, "safe_harbor", "dim_patient", ["MRN", "_ingest_ts"])
    assert "_ingest_ts" not in report.defaulted
    assert "_ingest_ts" not in report.classified


def test_coverage_is_clean_when_all_present_and_classified(cfg):
    prof = cfg["profiles"]["safe_harbor"]["tables"]["dim_patient"]
    report = audit_coverage(cfg, "safe_harbor", "dim_patient", list(prof.keys()))
    assert report.is_clean
