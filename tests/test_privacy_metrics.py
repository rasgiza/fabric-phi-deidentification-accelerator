"""Unit tests for privacy_metrics: k-anonymity, l-diversity, t-closeness, enforcement."""

from fabric_phi_deid.privacy_metrics import (
    enforce_k_anonymity,
    equivalence_classes,
    measure_k_anonymity,
    measure_l_diversity,
    measure_t_closeness,
)

QIS = ["birth_year", "sex", "zip3"]


def _records():
    # Three equivalence classes: (1990,M,100) x3, (1990,F,100) x2, (1985,M,200) x1
    return [
        {"birth_year": 1990, "sex": "M", "zip3": "100", "dx": "flu"},
        {"birth_year": 1990, "sex": "M", "zip3": "100", "dx": "cold"},
        {"birth_year": 1990, "sex": "M", "zip3": "100", "dx": "flu"},
        {"birth_year": 1990, "sex": "F", "zip3": "100", "dx": "flu"},
        {"birth_year": 1990, "sex": "F", "zip3": "100", "dx": "flu"},
        {"birth_year": 1985, "sex": "M", "zip3": "200", "dx": "diabetes"},
    ]


def test_equivalence_classes_groups_by_qi_tuple():
    classes = equivalence_classes(_records(), QIS)
    assert len(classes) == 3
    assert sorted(len(v) for v in classes.values()) == [1, 2, 3]


def test_k_anonymity_reports_min_class_size():
    report = measure_k_anonymity(_records(), QIS, k=2)
    assert report.k == 1  # the singleton class drives k down
    assert report.num_records == 6
    assert report.num_classes == 3
    assert report.violating_classes == 1
    assert report.violating_records == 1
    assert report.passes is False
    assert report.class_size_histogram == {1: 1, 2: 1, 3: 1}


def test_k_anonymity_passes_when_threshold_met():
    recs = _records()[:5]  # drop the singleton
    report = measure_k_anonymity(recs, QIS, k=2)
    assert report.k == 2
    assert report.passes is True
    assert report.violating_records == 0


def test_k_anonymity_examples_opt_in():
    no_ex = measure_k_anonymity(_records(), QIS, k=2)
    assert no_ex.smallest_class_examples == []
    with_ex = measure_k_anonymity(_records(), QIS, k=2, include_examples=True, top=1)
    assert with_ex.smallest_class_examples[0][1] == 1  # smallest class size


def test_l_diversity_counts_distinct_sensitive_values():
    report = measure_l_diversity(_records(), QIS, "dx", l=2)
    # (1990,F,100) class {flu} -> l=1; (1985,M,200) singleton {diabetes} -> l=1. Both violate.
    assert report.l == 1
    assert report.passes is False
    assert report.violating_classes == 2
    assert report.violating_records == 3  # 2-row homogeneous class + 1-row singleton


def test_t_closeness_zero_when_class_matches_global():
    recs = [
        {"g": "a", "s": "x"},
        {"g": "a", "s": "y"},
        {"g": "b", "s": "x"},
        {"g": "b", "s": "y"},
    ]
    report = measure_t_closeness(recs, ["g"], "s", t=0.1)
    assert report.t == 0.0
    assert report.passes is True


def test_t_closeness_detects_skewed_class():
    recs = [
        {"g": "a", "s": "x"},
        {"g": "a", "s": "x"},
        {"g": "b", "s": "y"},
        {"g": "b", "s": "y"},
    ]
    report = measure_t_closeness(recs, ["g"], "s", t=0.1)
    # Each class is pure (p=1.0) vs a 50/50 global -> TV distance = 0.5*(0.5+0.5) = 0.5.
    assert report.t == 0.5
    assert report.passes is False


def test_enforce_k_anonymity_suppresses_small_classes():
    kept, report = enforce_k_anonymity(_records(), QIS, k=2)
    assert report.num_records_in == 6
    assert report.num_records_out == 5  # singleton dropped
    assert report.suppressed_records == 1
    assert report.suppressed_classes == 1
    assert report.retained_classes == 2
    # Verify the result truly meets the k floor.
    assert measure_k_anonymity(kept, QIS, k=2).passes is True


def test_enforce_rejects_bad_k():
    import pytest

    with pytest.raises(ValueError):
        enforce_k_anonymity(_records(), QIS, k=0)
