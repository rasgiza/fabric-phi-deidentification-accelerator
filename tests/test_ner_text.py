"""Unit tests for ner_text. Forces the regex backend for deterministic, dependency-free runs."""

import pytest

from fabric_phi_deid.ner_text import (
    TextFinding,
    analyze_text,
    redact_text,
    scan_texts,
)

PEPPER = "unit-test-pepper-not-a-real-secret-0123456789"
SAMPLE = "Call 212-555-0123 or email jane.doe@example.com; SSN 123-45-6789."


def test_analyze_text_finds_structured_identifiers_regex():
    findings = analyze_text(SAMPLE, backend="regex")
    types = {f.entity_type for f in findings}
    assert {"PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN"} <= types


def test_analyze_text_sorted_by_offset():
    findings = analyze_text(SAMPLE, backend="regex")
    starts = [f.start for f in findings]
    assert starts == sorted(starts)


def test_analyze_text_empty_and_none():
    assert analyze_text("", backend="regex") == []
    assert analyze_text(None, backend="regex") == []


def test_analyze_text_excludes_substring_by_default():
    findings = analyze_text(SAMPLE, backend="regex")
    assert all(f.text is None for f in findings)


def test_analyze_text_include_text_opt_in():
    findings = analyze_text(SAMPLE, entities=["EMAIL_ADDRESS"], backend="regex", include_text=True)
    assert findings and findings[0].text == "jane.doe@example.com"


def test_redact_label_removes_clear_values():
    out = redact_text(SAMPLE, backend="regex", replacement="label")
    assert "212-555-0123" not in out
    assert "jane.doe@example.com" not in out
    assert "123-45-6789" not in out
    assert "[PHONE_NUMBER]" in out
    assert "[EMAIL_ADDRESS]" in out


def test_redact_remove_deletes_spans():
    out = redact_text(
        "email a@b.com now", entities=["EMAIL_ADDRESS"], backend="regex", replacement="remove"
    )
    assert "a@b.com" not in out
    assert out == "email  now"


def test_redact_token_is_deterministic_and_keyed():
    text = "email a@b.com"
    out1 = redact_text(
        text, entities=["EMAIL_ADDRESS"], backend="regex", replacement="token", pepper=PEPPER
    )
    out2 = redact_text(
        text, entities=["EMAIL_ADDRESS"], backend="regex", replacement="token", pepper=PEPPER
    )
    assert out1 == out2
    assert "a@b.com" not in out1
    assert "[EMAIL_ADDRESS:" in out1


def test_redact_token_requires_pepper():
    with pytest.raises(ValueError):
        redact_text("a@b.com", entities=["EMAIL_ADDRESS"], backend="regex", replacement="token")


def test_redact_invalid_replacement_raises():
    with pytest.raises(ValueError):
        redact_text("x", backend="regex", replacement="nonsense")


def test_analyze_invalid_backend_raises():
    with pytest.raises(ValueError):
        analyze_text("x", backend="nope")


def test_scan_texts_aggregates_counts():
    counts = scan_texts(["a@b.com", "c@d.com", None, "no pii here"], backend="regex")
    assert counts.get("EMAIL_ADDRESS") == 2


def test_redact_no_findings_returns_input_unchanged():
    text = "nothing to see here"
    assert redact_text(text, backend="regex") == text


def test_merge_overlapping_spans_via_supplied_findings():
    # Two overlapping spans should collapse into one replacement.
    text = "abcdefghij"
    findings = [
        TextFinding("A", 2, 6, 0.9),
        TextFinding("B", 4, 8, 0.5),
    ]
    out = redact_text(text, findings=findings, replacement="label")
    assert out == "ab[A]ij"


def test_engine_redact_text_strategy_integration():
    # The config-driven engine dispatches free-text columns through ner_text.
    from fabric_phi_deid.deid_engine import apply_strategy

    out = apply_strategy(
        "patient jane.doe@example.com called",
        "redact_text",
        {"replacement": "label", "backend": "regex", "entities": ["EMAIL_ADDRESS"]},
        PEPPER,
    )
    assert "jane.doe@example.com" not in out
    assert "[EMAIL_ADDRESS]" in out


def test_engine_redact_text_passthrough_empty():
    from fabric_phi_deid.deid_engine import apply_strategy

    assert apply_strategy(None, "redact_text", {"backend": "regex"}, PEPPER) is None
    assert apply_strategy("", "redact_text", {"backend": "regex"}, PEPPER) == ""
