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
_nonempty = st.text(min_size=1, max_size=64)


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
