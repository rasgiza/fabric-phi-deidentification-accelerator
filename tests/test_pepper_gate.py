"""The published demo pepper must not silently tokenize anything.

The failure this guards against is subtle: the demo pepper is 64 high-entropy characters, so
every strength check passes it. The problem is not that it is weak, it is that it is *printed
in a public repository*. A shared key across all deployments, over an identifier space as small
and structured as MRNs, makes the tokens invertible by anyone who can read the repo -- which
contradicts the one guarantee tokenization is supposed to provide.
"""

import hashlib
import os

import pytest

from fabric_phi_deid.tokenization import (
    ALLOW_COMPROMISED_PEPPER_ENV,
    COMPROMISED_PEPPER_ACK,
    KEYVAULT_URL_ENV,
    MIN_PEPPER_LENGTH,
    PEPPER_ENV,
    get_pepper,
    is_known_compromised_pepper,
)

# The literal that appears in notebooks/02b_silver_deid.ipynb and notebooks/NB_reidentify.ipynb.
# Kept here so the test fails loudly if the notebooks ever change it without updating the
# blocklist -- a rotated demo pepper that nobody added to the blocklist is the exact regression
# this file exists to catch.
DEMO_PEPPER = "xbSJJefaA60C_s2oNjPKr3t7Z1BQaGv9Go9TH5rse-2kGAZHdVGBwA9mnItp0COf"  # noqa: S105
GOOD_PEPPER = "n0t-th3-d3m0-p3pp3r-but-still-long-enough-to-pass-the-length-bar"  # noqa: S105


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test starts from a known-empty pepper environment."""
    for var in (PEPPER_ENV, ALLOW_COMPROMISED_PEPPER_ENV, KEYVAULT_URL_ENV):
        monkeypatch.delenv(var, raising=False)


# --- the blocklist itself ------------------------------------------------------------------


def test_the_demo_pepper_is_recognised_as_compromised():
    assert is_known_compromised_pepper(DEMO_PEPPER)


def test_the_demo_pepper_would_pass_a_naive_strength_check():
    # Documents WHY a length/entropy bar is insufficient: this value clears it comfortably.
    assert len(DEMO_PEPPER) > MIN_PEPPER_LENGTH


def test_an_unrelated_pepper_is_not_flagged():
    assert not is_known_compromised_pepper(GOOD_PEPPER)


@pytest.mark.parametrize("value", ["", None])
def test_empty_values_are_not_flagged_as_compromised(value):
    # Absent is a different failure from compromised; the length check owns that one.
    assert not is_known_compromised_pepper(value)


def test_a_near_miss_is_not_flagged():
    # One character different is a different secret entirely -- no fuzzy matching.
    assert not is_known_compromised_pepper(DEMO_PEPPER[:-1] + "X")


def test_the_blocklist_stores_digests_not_the_secret():
    """The source file must not contain a usable pepper."""
    from pathlib import Path

    import fabric_phi_deid.tokenization as tok

    source = Path(tok.__file__).read_text(encoding="utf-8")
    assert DEMO_PEPPER not in source
    assert hashlib.sha256(DEMO_PEPPER.encode()).hexdigest() in source


# --- the gate ------------------------------------------------------------------------------


def test_get_pepper_refuses_the_demo_pepper_by_default(monkeypatch):
    monkeypatch.setenv(PEPPER_ENV, DEMO_PEPPER)
    with pytest.raises(ValueError, match="published demo pepper"):
        get_pepper()


def test_the_refusal_explains_both_ways_out(monkeypatch):
    monkeypatch.setenv(PEPPER_ENV, DEMO_PEPPER)
    with pytest.raises(ValueError) as exc:
        get_pepper()
    message = str(exc.value)
    assert "secrets.token_urlsafe(48)" in message  # the real fix
    assert ALLOW_COMPROMISED_PEPPER_ENV in message  # the synthetic-data escape
    assert COMPROMISED_PEPPER_ACK in message
    assert DEMO_PEPPER not in message  # never echo the secret back


def test_get_pepper_allows_the_demo_pepper_with_the_explicit_acknowledgement(monkeypatch):
    monkeypatch.setenv(PEPPER_ENV, DEMO_PEPPER)
    monkeypatch.setenv(ALLOW_COMPROMISED_PEPPER_ENV, COMPROMISED_PEPPER_ACK)
    assert get_pepper() == DEMO_PEPPER


@pytest.mark.parametrize("ack", ["1", "true", "TRUE", "yes", "y", "on", "", "synthetic"])
def test_a_truthy_value_is_not_an_acknowledgement(monkeypatch, ack):
    """The gate must not be flippable by habit.

    `=1` gets set once in a base image and never reconsidered. Requiring the exact phrase
    forces a statement about the data rather than a reflex.
    """
    monkeypatch.setenv(PEPPER_ENV, DEMO_PEPPER)
    monkeypatch.setenv(ALLOW_COMPROMISED_PEPPER_ENV, ack)
    with pytest.raises(ValueError, match="published demo pepper"):
        get_pepper()


def test_the_acknowledgement_does_not_whitelist_other_weaknesses(monkeypatch):
    """Acknowledging the demo pepper must not turn off the length check."""
    monkeypatch.setenv(PEPPER_ENV, "short")
    monkeypatch.setenv(ALLOW_COMPROMISED_PEPPER_ENV, COMPROMISED_PEPPER_ACK)
    with pytest.raises(ValueError, match="too short"):
        get_pepper()


def test_a_good_pepper_is_unaffected(monkeypatch):
    monkeypatch.setenv(PEPPER_ENV, GOOD_PEPPER)
    assert get_pepper() == GOOD_PEPPER


def test_the_gate_applies_to_the_key_vault_path_too(monkeypatch):
    """A compromised value is refused regardless of how it was resolved.

    Both paths funnel through the same validator, so a pepper copied into Key Vault gets the
    same treatment as one set in an env var.
    """
    import sys
    import types

    fake = types.ModuleType("notebookutils")
    fake.credentials = types.SimpleNamespace(getSecret=lambda url, name: DEMO_PEPPER)
    monkeypatch.setitem(sys.modules, "notebookutils", fake)
    monkeypatch.setenv(KEYVAULT_URL_ENV, "https://example.vault.azure.net/")

    with pytest.raises(ValueError, match="published demo pepper"):
        get_pepper()

    monkeypatch.setenv(ALLOW_COMPROMISED_PEPPER_ENV, COMPROMISED_PEPPER_ACK)
    assert get_pepper() == DEMO_PEPPER


# --- the notebooks must not bypass the gate ------------------------------------------------


@pytest.mark.parametrize("notebook", ["02b_silver_deid.ipynb", "NB_reidentify.ipynb"])
def test_notebooks_that_ship_the_demo_pepper_also_ship_the_acknowledgement(notebook):
    """A notebook carrying the demo pepper must set the ack, or it is simply broken.

    This is the regression that would otherwise be found by a user, in Fabric, at run time.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "notebooks" / notebook
    text = path.read_text(encoding="utf-8")
    if DEMO_PEPPER not in text:
        pytest.skip(f"{notebook} no longer ships the demo pepper")
    assert ALLOW_COMPROMISED_PEPPER_ENV in text
    assert COMPROMISED_PEPPER_ACK in text


def test_no_notebook_acknowledges_a_pepper_it_does_not_ship():
    """The ack must never be set globally 'just in case'.

    Setting it where no demo pepper is used would silently pre-authorise a future one.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in list((root / "notebooks").glob("*.ipynb")) + list(root.glob("*.ipynb")):
        text = path.read_text(encoding="utf-8")
        if ALLOW_COMPROMISED_PEPPER_ENV in text and "os.environ[" in text:
            assert DEMO_PEPPER in text, (
                f"{path.name} sets {ALLOW_COMPROMISED_PEPPER_ENV} but ships no demo pepper"
            )


def test_the_environment_is_left_clean():
    """Guard against a test leaking the acknowledgement into the rest of the suite."""
    assert os.environ.get(ALLOW_COMPROMISED_PEPPER_ENV) is None
