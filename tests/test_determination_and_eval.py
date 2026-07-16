"""Unit tests for eval_harness metrics and time-limited determination manifest metadata."""

from datetime import UTC, datetime, timedelta

from fabric_phi_deid.audit import build_run_manifest
from fabric_phi_deid.eval_harness import (
    ClassificationMetrics,
    GoldSpan,
    evaluate_flags,
    evaluate_sets,
    evaluate_spans,
)
from fabric_phi_deid.ner_text import TextFinding

_CFG = {
    "active_profile": "safe_harbor",
    "profiles": {"safe_harbor": {"default_strategy": "suppress", "tables": {}}},
}


def test_metrics_math():
    m = ClassificationMetrics(true_positives=8, false_positives=2, false_negatives=2)
    assert m.support == 10
    assert m.precision == 0.8
    assert m.recall == 0.8
    assert abs(m.f1 - 0.8) < 1e-9
    d = m.to_dict()
    assert d["precision"] == 0.8 and d["f1"] == 0.8


def test_metrics_perfect_and_empty():
    perfect = ClassificationMetrics(5, 0, 0)
    assert perfect.precision == 1.0 and perfect.recall == 1.0 and perfect.f1 == 1.0
    # No predictions and no gold -> vacuously perfect precision/recall, f1 falls out at 1.0.
    empty = ClassificationMetrics(0, 0, 0)
    assert empty.precision == 1.0 and empty.recall == 1.0


def test_evaluate_sets():
    m = evaluate_sets(predicted={1, 2, 3}, gold={2, 3, 4})
    assert m.true_positives == 2
    assert m.false_positives == 1
    assert m.false_negatives == 1


def test_evaluate_flags():
    pairs = [(True, True), (True, False), (False, True), (False, False)]
    m = evaluate_flags(pairs)
    assert m.true_positives == 1
    assert m.false_positives == 1
    assert m.false_negatives == 1


def test_evaluate_spans_type_and_overlap():
    gold = [GoldSpan(0, 10, "PERSON"), GoldSpan(20, 30, "US_SSN")]
    pred = [
        TextFinding("PERSON", 0, 9, 0.9),  # strong overlap, right type -> TP
        TextFinding("US_SSN", 40, 50, 0.9),  # no overlap -> FP; gold SSN -> FN
    ]
    m = evaluate_spans(pred, gold, min_overlap=0.5)
    assert m.true_positives == 1
    assert m.false_positives == 1
    assert m.false_negatives == 1


def test_evaluate_spans_type_mismatch_is_not_a_match():
    gold = [GoldSpan(0, 10, "PERSON")]
    pred = [TextFinding("LOCATION", 0, 10, 0.9)]
    m = evaluate_spans(pred, gold, match_type=True)
    assert m.true_positives == 0
    assert m.false_positives == 1
    assert m.false_negatives == 1
    # Same spans match when type is ignored.
    m2 = evaluate_spans(pred, gold, match_type=False)
    assert m2.true_positives == 1


def test_evaluate_spans_one_gold_consumed_once():
    gold = [GoldSpan(0, 10, "PERSON")]
    pred = [TextFinding("PERSON", 0, 10, 0.9), TextFinding("PERSON", 1, 9, 0.8)]
    m = evaluate_spans(pred, gold)
    assert m.true_positives == 1
    assert m.false_positives == 1  # second prediction has no remaining gold to match


# ---------------------------------------------------------------------------
# Time-limited determination metadata
# ---------------------------------------------------------------------------
def _manifest(expires_utc=None):
    return build_run_manifest(
        _CFG,
        "safe_harbor",
        actor="tester",
        tables={"dim_patient": {"columns": ["PatientKey"]}},
        determination_method="expert_determination",
        determination_expires_utc=expires_utc,
        determination_reviewer="Jane Statistician, PhD",
    )


def test_manifest_records_determination_metadata():
    m = _manifest("2099-01-01T00:00:00+00:00")
    assert m.determination_method == "expert_determination"
    assert m.determination_reviewer == "Jane Statistician, PhD"
    # Round-trips through the PHI-free dict/json form.
    assert m.to_dict()["determination_expires_utc"] == "2099-01-01T00:00:00+00:00"


def test_determination_not_expired_in_future():
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    assert _manifest(future).is_determination_expired() is False


def test_determination_expired_in_past():
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert _manifest(past).is_determination_expired() is True


def test_determination_expiry_none_when_unset():
    assert _manifest(None).is_determination_expired() is None


def test_determination_naive_timestamp_treated_as_utc():
    past_naive = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None).isoformat()
    assert _manifest(past_naive).is_determination_expired() is True
