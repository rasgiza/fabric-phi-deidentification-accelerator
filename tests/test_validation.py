"""Tests for the PHI leak scanner (validation.py)."""

from __future__ import annotations

from fabric_phi_deid.validation import scan_value_for_phi, scan_values_for_phi


def test_detects_ssn():
    assert "ssn" in scan_value_for_phi("123-45-6789")
    assert "ssn" in scan_value_for_phi("Patient SSN 123456789 on file")


def test_detects_phone():
    assert "phone" in scan_value_for_phi("(212) 555-0123")
    assert "phone" in scan_value_for_phi("+1 212-555-0123")


def test_detects_email():
    assert "email" in scan_value_for_phi("jane.doe@example.com")


def test_clean_values_have_no_hits():
    assert scan_value_for_phi("PT-a1b2c3d4e5f6a7b8") == []
    assert scan_value_for_phi("Rivera, Alex") == []
    assert scan_value_for_phi(1984) == []
    assert scan_value_for_phi(None) == []


def test_aggregate_counts():
    values = ["123-45-6789", "clean", "jane@example.com", "also clean"]
    hits = scan_values_for_phi(values)
    assert hits.get("ssn") == 1
    assert hits.get("email") == 1
    assert "phone" not in hits
