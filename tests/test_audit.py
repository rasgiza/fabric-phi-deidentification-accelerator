"""Tests for the audit / run-manifest module — determinism and PHI-safety."""

from __future__ import annotations

import json

from fabric_phi_deid.audit import (
    build_run_manifest,
    config_fingerprint,
    summarize_table_plan,
    write_manifest,
)


def test_fingerprint_is_deterministic_and_order_independent(cfg):
    fp1 = config_fingerprint(cfg)
    reordered = {"profiles": cfg["profiles"], "active_profile": cfg.get("active_profile")}
    fp2 = config_fingerprint(reordered)
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_fingerprint_changes_when_rule_changes(cfg):
    import copy

    mutated = copy.deepcopy(cfg)
    mutated["profiles"]["safe_harbor"]["tables"]["dim_patient"]["MRN"] = "suppress"
    assert config_fingerprint(mutated) != config_fingerprint(cfg)


def test_summarize_table_plan_counts_strategies(cfg):
    columns = ["MRN", "DateOfBirth", "PatientKey", "Mystery"]
    summary = summarize_table_plan(cfg, "safe_harbor", "dim_patient", columns)
    assert summary["counts"]["tokenize"] == 1  # MRN
    assert summary["counts"]["generalize"] == 1  # DateOfBirth
    assert summary["counts"]["passthrough"] == 1  # PatientKey
    assert summary["counts"]["suppress"] == 1  # Mystery (deny-by-default)
    assert "MRN" in summary["columns_by_strategy"]["tokenize"]


def test_manifest_has_no_data_values(cfg, pepper):
    tables = {
        "dim_patient": {
            "columns": ["MRN", "DateOfBirth", "PatientKey"],
            "input_rows": 100,
            "output_rows": 100,
        }
    }
    manifest = build_run_manifest(
        cfg,
        "safe_harbor",
        actor="tester@example.com",
        tables=tables,
        pepper_key_version="v3",
    )
    blob = manifest.to_json()
    # Manifest carries counts + column NAMES + metadata only — never data values, and
    # only a non-sensitive pepper *key version* label (never the pepper secret itself).
    assert pepper not in blob
    assert "unit-test-pepper" not in blob
    parsed = json.loads(blob)
    assert parsed["pepper_key_version"] == "v3"
    assert parsed["profile"] == "safe_harbor"
    assert parsed["config_sha256"] == config_fingerprint(cfg)
    assert parsed["tables"][0]["input_rows"] == 100
    assert parsed["run_id"]  # a uuid was assigned


def test_manifest_round_trips_to_disk(cfg, tmp_path):
    tables = {"dim_patient": {"columns": ["MRN", "PatientKey"]}}
    manifest = build_run_manifest(cfg, "safe_harbor", actor="ci", tables=tables)
    out = tmp_path / "manifest.json"
    write_manifest(manifest, str(out))
    reloaded = json.loads(out.read_text())
    assert reloaded["actor"] == "ci"
    assert reloaded["engine_version"]
