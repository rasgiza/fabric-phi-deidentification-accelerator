"""Shared pytest fixtures and path setup for the fabric_phi_deid test suite.

Adds ``src/`` to sys.path so tests import the package even without an editable install
(``pip install -e .``). Once installed, this is a harmless no-op.
"""

from __future__ import annotations

import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Never a real secret. Long enough to satisfy any local length checks.
PEPPER = "unit-test-pepper-not-a-real-secret-0123456789"

_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "deid_rules.yaml")


@pytest.fixture(scope="session")
def pepper() -> str:
    return PEPPER


@pytest.fixture(scope="session")
def rules_path() -> str:
    return _RULES_PATH


@pytest.fixture(scope="session")
def cfg() -> dict:
    from fabric_phi_deid.deid_engine import load_rules

    return load_rules(_RULES_PATH)
