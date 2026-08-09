"""Safe Harbor prong (ii): the condition software cannot check, and must not skip.

§164.514(b)(2) is two conditions joined by AND. Prong (i) — remove the 18 identifiers — is
mechanical, and it is the only one most tooling implements. Prong (ii) — no actual knowledge
that the residual data could identify someone — is a statement about what the covered entity
knows, and no scan can produce it.

The failure mode this module guards is not "the attestation is wrong". It is "the
attestation was never made, and nothing noticed". These tests exist because the shipped
config is *supposed* to fail, and a future well-meaning edit that makes the defaults look
tidy would quietly turn a hard stop into a green tick.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fabric_phi_deid.deid_engine import load_rules
from fabric_phi_deid.determination import (
    MIN_ATTESTATION_STATEMENT_CHARS,
    ActualKnowledgeAttestation,
    load_actual_knowledge_attestation,
)

CONFIG = Path(__file__).resolve().parents[1] / "config" / "deid_rules.yaml"

GOOD_STATEMENT = (
    "Reviewed the three other extracts this organisation publishes, confirmed none shares a "
    "joinable key with this release, and checked small-cell counts for rare diagnoses and "
    "single-provider specialties within each 3-digit ZIP."
)


def _attestation(**overrides) -> ActualKnowledgeAttestation:
    base = {
        "statement": GOOD_STATEMENT,
        "attested_by": "Dana Okafor",
        "role": "Privacy Officer",
        "applies_to": "gold_safe_* release 2026-Q3",
        "attested_utc": "2026-08-01",
        "expires_utc": (datetime.now(UTC) + timedelta(days=90)).date().isoformat(),
    }
    base.update(overrides)
    return ActualKnowledgeAttestation(**base)


@pytest.fixture(scope="module")
def shipped() -> dict:
    return load_rules(CONFIG)


def test_shipped_attestation_is_unsigned_and_therefore_unusable(shipped: dict) -> None:
    """The template must fail closed, exactly like the unsigned risk acceptances.

    An accelerator that shipped a pre-signed prong (ii) attestation would be handing every
    downstream user a Safe Harbor claim that nobody at their organisation ever made. The
    convenient default here is the dangerous one.
    """
    attestation = load_actual_knowledge_attestation(shipped)
    assert attestation is not None, "the block must exist so its absence cannot be mistaken for N/A"
    assert not attestation.is_usable()
    assert attestation.defects()


def test_a_complete_in_date_attestation_is_usable() -> None:
    assert _attestation().is_usable()
    assert _attestation().defects() == []


def test_a_lapsed_attestation_is_not_usable() -> None:
    """Actual knowledge is a statement about a moment, not a permanent property."""
    lapsed = _attestation(expires_utc="2020-01-01")
    assert not lapsed.is_usable()
    assert any("expired" in d for d in lapsed.defects())


def test_an_unparseable_expiry_counts_as_expired() -> None:
    """Fail closed on a malformed date rather than treating it as no deadline at all."""
    assert _attestation(expires_utc="whenever").is_expired()


def test_a_one_line_statement_is_a_signature_on_nothing() -> None:
    short = _attestation(statement="No actual knowledge.")
    assert not short.is_usable()
    assert any(str(MIN_ATTESTATION_STATEMENT_CHARS) in d for d in short.defects())


@pytest.mark.parametrize("field", ["statement", "attested_by", "role", "applies_to", "expires_utc"])
def test_an_unedited_placeholder_cannot_attest(field: str) -> None:
    assert not _attestation(**{field: "TODO"}).is_usable()


def test_a_missing_block_returns_none_rather_than_an_empty_pass() -> None:
    """``None`` is not "nothing to check" — the scorecard must read it as a FAIL.

    Returning a blank-but-usable attestation here would be the worst possible behaviour:
    deleting the block from the config would *strengthen* the run.
    """
    assert load_actual_knowledge_attestation({"profiles": {}}) is None
    assert load_actual_knowledge_attestation("not a config") is None


def test_a_malformed_block_reports_defects_instead_of_vanishing() -> None:
    attestation = load_actual_knowledge_attestation({"actual_knowledge": "signed, trust me"})
    assert attestation is not None
    assert not attestation.is_usable()


def test_residual_risks_accepts_a_bare_string() -> None:
    """People write one risk without a list; that must not become a per-character list."""
    attestation = load_actual_knowledge_attestation(
        {"actual_knowledge": {"residual_risks": "rare diagnosis codes in ZIP 104"}}
    )
    assert attestation is not None
    assert attestation.residual_risks == ("rare diagnosis codes in ZIP 104",)


def test_to_dict_carries_the_defects_into_the_evidence_artifact() -> None:
    """A manifest that recorded only `usable: false` would hide *why*."""
    payload = _attestation(expires_utc="2020-01-01").to_dict()
    assert payload["usable"] is False
    assert payload["expired"] is True
    assert payload["defects"]


def test_the_shipped_block_declares_what_this_pipeline_does_not_cover(shipped: dict) -> None:
    """The modality limits must travel with the artifact, not live in a slide deck.

    HIPAA compliance is a shared responsibility: the platform supplies capabilities and a
    BAA, and the covered entity makes the determination and carries the liability. The
    honest failure mode for an accelerator is not a wrong answer, it is silence about the
    PHI it never looked at. DICOM pixel data, ambient voice, biometric templates and
    full-face images are all PHI, and none of them pass through this pipeline -- so the
    shipped attestation says so in every run manifest and every scorecard.
    """
    attestation = load_actual_knowledge_attestation(shipped)
    assert attestation is not None
    blob = " ".join(attestation.residual_risks).lower()
    for out_of_scope in ("dicom", "voice", "biometric", "photograph"):
        assert out_of_scope in blob, f"shipped residual_risks never mentions {out_of_scope}"


def test_prefilled_residual_risks_do_not_sign_anything(shipped: dict) -> None:
    """Listing risks is not attesting to them; the block must still fail closed.

    ``residual_risks`` is informational and deliberately absent from ``defects()``. If it
    ever started counting toward usability, a helpful maintainer filling in the known
    limits would silently hand every downstream user a Safe Harbor claim nobody signed.
    """
    attestation = load_actual_knowledge_attestation(shipped)
    assert attestation is not None
    assert attestation.residual_risks, "the known limits should ship pre-filled"
    assert not attestation.is_usable()
    assert _attestation(residual_risks=()).is_usable(), "risks are not a usability input"
