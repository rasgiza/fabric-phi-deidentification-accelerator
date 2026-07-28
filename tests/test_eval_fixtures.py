"""Integrity tests for the shipped free-text recall fixture (eval_fixtures).

These guard the fixture the scorecard relies on: offsets must be valid, entity types must be
ones ner_text can emit, and the dependency-free regex backend must recover exactly the
structured identifiers (a deterministic, Presidio-free baseline that CI can assert).
"""

from fabric_phi_deid.eval_fixtures import FREE_TEXT_PHI_NOTES, gold_span_count
from fabric_phi_deid.eval_harness import ClassificationMetrics, evaluate_spans
from fabric_phi_deid.ner_text import DEFAULT_ENTITIES, analyze_text

# Entity types the dependency-free regex fallback can match reliably.
_STRUCTURED = {"US_SSN", "PHONE_NUMBER", "EMAIL_ADDRESS", "CREDIT_CARD", "IP_ADDRESS", "URL"}
# Contextual types that require the Presidio backend.
_CONTEXTUAL = {"PERSON", "LOCATION", "DATE_TIME"}


def test_fixture_offsets_are_valid_and_ordered():
    for text, spans in FREE_TEXT_PHI_NOTES:
        assert text, "note text must be non-empty"
        for s in spans:
            assert 0 <= s.start < s.end <= len(text), f"bad span {s} for text len {len(text)}"
            assert s.entity_type in DEFAULT_ENTITIES, f"unknown entity type {s.entity_type}"


def test_fixture_span_count_is_stable():
    # 18 labeled spans, balanced across structured and contextual identifiers.
    assert gold_span_count() == 18
    structured = sum(
        1 for _t, spans in FREE_TEXT_PHI_NOTES for s in spans if s.entity_type in _STRUCTURED
    )
    contextual = sum(
        1 for _t, spans in FREE_TEXT_PHI_NOTES for s in spans if s.entity_type in _CONTEXTUAL
    )
    assert structured == 9
    assert contextual == 9
    assert structured + contextual == gold_span_count()


def test_regex_backend_recovers_all_structured_identifiers():
    # Deterministic baseline: the regex fallback finds every structured span with no false
    # positives, and (correctly) misses the contextual half -> recall 0.5 overall.
    tp = fp = fn = 0
    for text, gold in FREE_TEXT_PHI_NOTES:
        preds = analyze_text(text, backend="regex")
        m = evaluate_spans(preds, list(gold), min_overlap=0.5, match_type=True)
        tp += m.true_positives
        fp += m.false_positives
        fn += m.false_negatives
    overall = ClassificationMetrics(tp, fp, fn)
    assert overall.true_positives == 9
    assert overall.false_positives == 0
    assert overall.false_negatives == 9
    assert overall.precision == 1.0
    assert overall.recall == 0.5
