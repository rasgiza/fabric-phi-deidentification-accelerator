"""Tests for cross-source patient linkage: normalization and the linkage-yield guard.

These cover the failure mode that a full pipeline run does NOT catch. When the two
sources fail to link, every notebook still succeeds, every publish gate still passes, and
the only symptom is that ``SourceSystem`` is never ``'Both'``. Nothing asserted that,
which is exactly how the shipped sample data went out with zero MRN overlap.
"""

from __future__ import annotations

import pytest

from fabric_phi_deid import gold_conform as gc
from fabric_phi_deid.tokenization import (
    normalize_identifier,
    tokenize,
    tokenize_format_preserving,
    tokenize_numeric,
)

PEPPER = "test-pepper-that-is-long-enough-to-pass-validation"


# --------------------------------------------------------------------------------------
# normalization: the whole point is that two systems writing the same MRN differently
# still tokenize to the same value, so the conformed dimension can join them.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caboodle_value, clarity_value",
    [
        ("MRN00001234", "mrn00001234"),        # case
        ("MRN00001234", "  MRN00001234  "),    # padding
        ("MRN00001234", "MRN00001234\n"),      # trailing newline from a CSV export
    ],
)
def test_cosmetic_differences_tokenize_identically(caboodle_value, clarity_value):
    """Case and padding must not break linkage -- this is the silent-zero-match bug."""
    assert tokenize(caboodle_value, PEPPER, namespace="mrn") == tokenize(
        clarity_value, PEPPER, namespace="mrn"
    )


def test_internal_spacing_is_collapsed_not_removed():
    """Runs of whitespace collapse to one space; the space itself is still significant.

    Removing it entirely is what ``strip_separators`` is for, and that is opt-in because
    it can merge genuinely different identifiers.
    """
    assert normalize_identifier("MRN  0000  1234") == "MRN 0000 1234"
    assert tokenize("MRN  0000  1234", PEPPER) == tokenize("MRN 0000 1234", PEPPER)
    assert tokenize("MRN 00001234", PEPPER) != tokenize("MRN00001234", PEPPER)


def test_strip_separators_is_opt_in():
    assert normalize_identifier("A12-3456") == "A12-3456"
    assert normalize_identifier("A12-3456", strip_separators=True) == "A123456"
    assert tokenize("A12-3456", PEPPER) != tokenize("A123456", PEPPER)
    assert tokenize("A12-3456", PEPPER, strip_separators=True) == tokenize(
        "A123456", PEPPER, strip_separators=True
    )


def test_normalization_does_not_merge_distinct_identifiers():
    """Under-normalizing costs a match; over-normalizing merges two patients. Assert the
    normalizations we deliberately did NOT implement stay unimplemented."""
    assert tokenize("00001234", PEPPER) != tokenize("1234", PEPPER)      # leading zeros
    assert tokenize("MRN1234", PEPPER) != tokenize("1234", PEPPER)       # prefix
    assert tokenize("E1234", PEPPER) != tokenize("H1234", PEPPER)        # assigning authority


@pytest.mark.parametrize("blank", [None, "", "   ", "\t", "\n"])
def test_blank_values_are_never_tokenized(blank):
    """A whitespace-only field is missing data, not an identifier.

    Tokenizing it would mint a real-looking token AND give every blank field in the
    dataset the same token -- an accidental join key linking unrelated patients.
    """
    assert tokenize(blank, PEPPER) == blank
    assert tokenize_numeric(blank, PEPPER) == blank
    assert tokenize_format_preserving(blank, PEPPER) == blank


def test_namespaces_still_do_not_collide_after_normalization():
    """Normalization must not weaken the cross-column separation guarantee."""
    assert tokenize("1234", PEPPER, namespace="mrn") != tokenize(
        "1234", PEPPER, namespace="npi"
    )


def test_format_preserving_keeps_shape_but_links_across_case():
    """Digest over the normalized value, shape from the original."""
    upper = tokenize_format_preserving("AB12-3456", PEPPER)
    lower = tokenize_format_preserving("ab12-3456", PEPPER)
    assert upper is not None and lower is not None
    assert upper != lower                      # casing preserved per character class
    assert upper.upper() == lower.upper()      # ...but driven by the same digest
    assert upper[4] == "-" and lower[4] == "-"  # separator preserved in place


# --------------------------------------------------------------------------------------
# linkage yield: make an unlinked run fail loudly instead of publishing a star with no
# 'Both' rows.
# --------------------------------------------------------------------------------------


def test_zero_overlap_is_rejected():
    """The exact shape of the shipped sample data before it was fixed."""
    report = gc.assess_linkage(caboodle_patients=50_000, clarity_patients=1_000, matched=0)
    assert report.status == "implausible"
    with pytest.raises(ValueError, match="linked across sources"):
        report.raise_if_implausible()


def test_healthy_overlap_passes_and_predicts_the_row_counts():
    report = gc.assess_linkage(caboodle_patients=50_000, clarity_patients=1_000, matched=800)
    assert report.status == "ok"
    assert report.linkage_rate == pytest.approx(0.80)
    assert report.matched == 800            # SourceSystem='Both'
    assert report.caboodle_only == 49_200   # SourceSystem='Caboodle'
    assert report.clarity_only == 200       # SourceSystem='Clarity'
    assert report.conformed_rows == 50_200  # the union, not the 51,000 sum
    report.raise_if_implausible()           # must not raise


def test_rate_is_scored_against_the_smaller_cohort():
    """1,000 Clarity patients matching 1,000 of 50,000 Caboodle patients is a PERFECT
    link, not a 2% one. Scoring against the larger side would flag a healthy run."""
    report = gc.assess_linkage(caboodle_patients=50_000, clarity_patients=1_000, matched=1_000)
    assert report.linkage_rate == pytest.approx(1.0)
    assert report.status == "ok"
    assert report.clarity_only == 0


def test_low_but_nonzero_overlap_warns_without_failing():
    report = gc.assess_linkage(caboodle_patients=10_000, clarity_patients=1_000, matched=50)
    assert report.status == "suspicious"
    report.raise_if_implausible()  # a real deployment may legitimately overlap little


def test_fan_out_is_caught():
    """More matches than patients means a duplicate key is multiplying rows -- which
    would silently duplicate every fact joining through PatientKey."""
    with pytest.raises(ValueError, match="fanned"):
        gc.assess_linkage(caboodle_patients=50_000, clarity_patients=1_000, matched=1_001)


def test_message_names_the_likely_causes():
    """The failure must tell an adopter what to check, not just that a number was low."""
    report = gc.assess_linkage(caboodle_patients=100, clarity_patients=100, matched=0)
    with pytest.raises(ValueError) as excinfo:
        report.raise_if_implausible()
    message = str(excinfo.value)
    assert "pepper" in message
    assert "namespace" in message
    assert gc.CLARITY_MRN_COLUMN in message
    assert gc.CABOODLE_MRN_COLUMN in message


def test_empty_clarity_extract_does_not_divide_by_zero():
    """A Caboodle-only deployment is supported: there is nothing to link, so the guard
    must stand down rather than report a 0% match rate as a failure."""
    report = gc.assess_linkage(caboodle_patients=50_000, clarity_patients=0, matched=0)
    assert report.linkage_rate == 0.0
    assert report.status == "not_applicable"
    assert report.conformed_rows == 50_000
    report.raise_if_implausible()  # must not raise
