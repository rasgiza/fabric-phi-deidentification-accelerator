"""Unit tests for determination: the Expert Determination evidence pack."""

from datetime import UTC, datetime

from fabric_phi_deid.determination import (
    DeterminationReport,
    ResidualScanResult,
    build_determination_report,
    residual_scan_from_hits,
)
from fabric_phi_deid.privacy_metrics import (
    measure_k_anonymity,
    measure_l_diversity,
    measure_t_closeness,
)

QIS = ["birth_year", "sex", "zip3"]


def _records():
    # Every equivalence class has >= 3 records and >= 2 distinct diagnoses.
    return [
        {"birth_year": 1990, "sex": "M", "zip3": "100", "dx": "flu"},
        {"birth_year": 1990, "sex": "M", "zip3": "100", "dx": "cold"},
        {"birth_year": 1990, "sex": "M", "zip3": "100", "dx": "flu"},
        {"birth_year": 1985, "sex": "F", "zip3": "200", "dx": "diabetes"},
        {"birth_year": 1985, "sex": "F", "zip3": "200", "dx": "asthma"},
        {"birth_year": 1985, "sex": "F", "zip3": "200", "dx": "diabetes"},
    ]


def _passing_report() -> DeterminationReport:
    recs = _records()
    return build_determination_report(
        method="expert_determination",
        config_sha256="abc123",
        engine_version="0.1.0",
        k_anonymity=measure_k_anonymity(recs, QIS, k=3),
        l_diversity=measure_l_diversity(recs, QIS, "dx", l=2),
        t_closeness=measure_t_closeness(recs, QIS, "dx", t=1.0),
        residual_scan=residual_scan_from_hits({}, tables_scanned=3, rows_scanned=6),
        reviewer="Dr. Reviewer",
        review_by_utc="2099-01-01T00:00:00+00:00",
    )


def test_residual_scan_from_hits_drops_zero_counts():
    result = residual_scan_from_hits({"ssn": 0, "email": 2}, tables_scanned=1, rows_scanned=100)
    assert result.pattern_hits == {"email": 2}
    assert result.clean is False


def test_clean_scan_reports_no_hits():
    result = ResidualScanResult(tables_scanned=2, rows_scanned=50)
    assert result.clean is True
    assert "PASS" in result.summary()


def test_report_passes_when_all_checks_pass():
    report = _passing_report()
    assert report.passes is True


def test_report_fails_when_residual_identifier_found():
    recs = _records()
    report = build_determination_report(
        method="expert_determination",
        config_sha256="abc123",
        engine_version="0.1.0",
        k_anonymity=measure_k_anonymity(recs, QIS, k=3),
        residual_scan=residual_scan_from_hits({"ssn": 1}, tables_scanned=3, rows_scanned=6),
    )
    assert report.passes is False


def test_report_fails_when_k_anonymity_below_threshold():
    recs = _records()
    report = build_determination_report(
        method="expert_determination",
        config_sha256="abc123",
        engine_version="0.1.0",
        k_anonymity=measure_k_anonymity(recs, QIS, k=10),  # impossible on 6 rows
    )
    assert report.passes is False


def test_omitted_checks_do_not_block_gate():
    report = build_determination_report(
        method="safe_harbor",
        config_sha256="abc123",
        engine_version="0.1.0",
    )
    assert report.passes is True


def test_review_expiry_detection():
    report = build_determination_report(
        method="expert_determination",
        config_sha256="abc123",
        engine_version="0.1.0",
        review_by_utc="2000-01-01T00:00:00+00:00",
    )
    assert report.is_review_expired(as_of=datetime(2026, 1, 1, tzinfo=UTC)) is True

    not_set = build_determination_report(
        method="safe_harbor", config_sha256="abc", engine_version="0.1.0"
    )
    assert not_set.is_review_expired() is None


def test_serialization_is_phi_free_and_roundtrips():
    import json

    report = _passing_report()
    as_dict = json.loads(report.to_json())
    assert as_dict["passes"] is True
    assert as_dict["method"] == "expert_determination"
    assert as_dict["config_sha256"] == "abc123"
    # markdown renders the overall verdict + the pre-real-PHI caveat
    md = report.to_markdown()
    assert "Expert Determination Evidence Pack" in md
    assert "pre_real_phi_checklist" in md
