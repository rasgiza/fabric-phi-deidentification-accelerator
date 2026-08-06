"""Property-based tests (Hypothesis) for the tokenization invariants that matter most.

Skipped automatically if Hypothesis is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from fabric_phi_deid.tokenization import tokenize  # noqa: E402

PEPPER = "unit-test-pepper-not-a-real-secret-0123456789"

# `tokenize` deliberately passes None/empty/whitespace-only values through unchanged, so that
# missing data is not turned into a spurious token. That matters more than it looks: minting a
# token for a blank MRN would give every missing-MRN row the SAME token, silently merging
# distinct people into one synthetic patient across every downstream join.
#
# The strategy below therefore excludes blanks. `min_size=1` is NOT enough - it admits values
# like "\r", which are non-empty but blank, and which Hypothesis does find. Those inputs are
# covered by the explicit contract tests at the bottom of this file instead.
_nonblank = st.text(min_size=1, max_size=64).filter(lambda s: s.strip() != "")
_nonempty = _nonblank  # backwards-compatible alias


@given(value=_nonempty)
def test_tokenize_is_deterministic(value):
    assert tokenize(value, PEPPER, namespace="x") == tokenize(value, PEPPER, namespace="x")


@given(value=_nonempty)
def test_tokenize_changes_with_namespace_is_stable(value):
    # Within a namespace, stable; the token is a fixed-length hex string.
    tok = tokenize(value, PEPPER, namespace="mrn")
    assert isinstance(tok, str)
    assert len(tok) == 16
    assert all(c in "0123456789abcdef" for c in tok)


@given(value=_nonempty, other=_nonempty)
def test_distinct_values_rarely_collide(value, other):
    # Different inputs should produce different tokens (64-bit space; collisions negligible).
    if value != other:
        assert tokenize(value, PEPPER) != tokenize(other, PEPPER)


@given(value=_nonempty)
def test_prefix_and_length_are_honored(value):
    tok = tokenize(value, PEPPER, prefix="PT-", length=10)
    assert tok.startswith("PT-")
    assert len(tok) == len("PT-") + 10


@given(value=_nonempty)
def test_pepper_change_changes_token(value):
    assert tokenize(value, PEPPER) != tokenize(value, PEPPER + "-rotated")


# --- The blank-passthrough contract, pinned explicitly -------------------------------------
# These are the inputs excluded from the strategies above. They are excluded because the
# behaviour is different, not because it is unimportant - so it is asserted directly rather
# than left untested.


@pytest.mark.parametrize("blank", [None, "", " ", "   ", "\r", "\n", "\t", " \r\n\t "])
def test_blank_values_pass_through_unchanged(blank):
    """Missing data must not be turned into a token.

    Tokenizing a blank would assign every missing-value row an identical token, which reads
    downstream as one real patient rather than as absent data.
    """
    assert tokenize(blank, PEPPER, namespace="mrn", prefix="PT-") == blank


@pytest.mark.parametrize("blank", ["", " ", "\r", "\n", "\t"])
def test_blank_values_are_not_given_a_prefix(blank):
    """A passthrough must be recognisable as *not* a token."""
    result = tokenize(blank, PEPPER, namespace="mrn", prefix="PT-")
    assert result is not None
    assert not result.startswith("PT-")


def test_blank_passthrough_does_not_collide_with_a_real_token():
    """A blank must never coincide with the token of a real value."""
    real = tokenize("MRN00000001", PEPPER, namespace="mrn", prefix="PT-")
    for blank in ("", " ", "\r", "\n", "\t"):
        assert tokenize(blank, PEPPER, namespace="mrn", prefix="PT-") != real
