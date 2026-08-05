"""Tests for the disclosure-risk gate: recorded risk acceptance and the k-anonymity remedy.

These cover the defect that made the accelerator's central claim unfalsifiable: k-anonymity —
the one metric that directly quantifies re-identification risk — was advisory, so it could
report ``k=1`` and still produce a green run. It is now a gate that fails closed.

A gate with no legitimate escape hatch would be worse, not better: some thresholds are
unreachable by construction (publishing birth year and 3-digit ZIP at patient grain spreads a
cohort over a quasi-identifier domain far larger than itself), and a control users cannot
satisfy is a control users disable. So the gate can be waived — but only by a written, scoped,
expiring acceptance that travels in the evidence artifact. These tests pin the difference
between "waived by a named human" and "silently passed".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from fabric_phi_deid.config import validate_config
from fabric_phi_deid.determination import (
    GATE_ACCEPTED_RISK,
    GATE_FAIL,
    GATE_PASS,
    MIN_ACCEPTANCE_REASON_CHARS,
    RiskAcceptance,
    evaluate_gate,
    load_risk_acceptance,
)
from fabric_phi_deid.privacy_metrics import suppression_cutoff

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "deid_rules.yaml"

FUTURE = (datetime.now(UTC) + timedelta(days=90)).isoformat()
PAST = (datetime.now(UTC) - timedelta(days=1)).isoformat()

GOOD_REASON = "Synthetic demo estate with no individual represented; residual risk accepted."


def _acceptance(**overrides) -> RiskAcceptance:
    fields = {
        "control": "k_anonymity",
        "reason": GOOD_REASON,
        "accepted_by": "A. Reviewer, Privacy Office",
        "applies_to": "synthetic demo estate",
        "expires_utc": FUTURE,
    }
    fields.update(overrides)
    return RiskAcceptance(**fields)


# ---------------------------------------------------------------------------------------
# The gate fails closed
# ---------------------------------------------------------------------------------------
def test_failing_measurement_without_acceptance_fails_the_run():
    outcome = evaluate_gate("k_anonymity", measured_pass=False, detail="k=1")
    assert outcome.status == GATE_FAIL
    assert outcome.passes is False


def test_passing_measurement_passes_without_any_acceptance():
    outcome = evaluate_gate("k_anonymity", measured_pass=True, detail="k=7")
    assert outcome.status == GATE_PASS
    assert outcome.passes is True


def test_a_pass_is_never_relabelled_as_accepted_risk():
    """An acceptance lying around must not downgrade a control that actually passed."""
    outcome = evaluate_gate("k_anonymity", measured_pass=True, acceptance=_acceptance())
    assert outcome.status == GATE_PASS


def test_valid_acceptance_turns_a_failure_into_accepted_risk_not_pass():
    outcome = evaluate_gate("k_anonymity", measured_pass=False, acceptance=_acceptance())
    assert outcome.status == GATE_ACCEPTED_RISK
    assert outcome.passes is True
    assert outcome.status != GATE_PASS


# ---------------------------------------------------------------------------------------
# An acceptance that is not really an acceptance cannot buy a pass
# ---------------------------------------------------------------------------------------
def test_expired_acceptance_fails_closed():
    outcome = evaluate_gate(
        "k_anonymity", measured_pass=False, acceptance=_acceptance(expires_utc=PAST)
    )
    assert outcome.status == GATE_FAIL
    assert any("expired" in d for d in outcome.acceptance_defects)


def test_unparseable_expiry_counts_as_expired():
    assert _acceptance(expires_utc="whenever").is_expired() is True


@pytest.mark.parametrize("field", ["reason", "accepted_by", "applies_to", "expires_utc"])
def test_blank_field_fails_closed(field):
    outcome = evaluate_gate(
        "k_anonymity", measured_pass=False, acceptance=_acceptance(**{field: "   "})
    )
    assert outcome.status == GATE_FAIL
    assert any(field in d for d in outcome.acceptance_defects)


@pytest.mark.parametrize("placeholder", ["TODO", "fixme", "CHANGEME", "<your name>", "TBD"])
def test_unedited_placeholder_fails_closed(placeholder):
    """Shipping a template must not accidentally sign off on residual re-identification risk."""
    outcome = evaluate_gate(
        "k_anonymity", measured_pass=False, acceptance=_acceptance(accepted_by=placeholder)
    )
    assert outcome.status == GATE_FAIL


def test_a_shrug_is_not_a_reason():
    outcome = evaluate_gate("k_anonymity", measured_pass=False, acceptance=_acceptance(reason="ok"))
    assert outcome.status == GATE_FAIL
    assert any(str(MIN_ACCEPTANCE_REASON_CHARS) in d for d in outcome.acceptance_defects)


def test_failure_summary_names_the_remedies():
    summary = evaluate_gate("k_anonymity", measured_pass=False, detail="k=1").summary()
    assert "no risk acceptance recorded" in summary
    assert "privacy_gates.k_anonymity.accepted_risk" in summary


def test_accepted_risk_summary_names_the_signer_and_the_expiry():
    summary = evaluate_gate("k_anonymity", measured_pass=False, acceptance=_acceptance()).summary()
    assert "ACCEPTED_RISK" in summary
    assert "A. Reviewer, Privacy Office" in summary
    assert FUTURE in summary


def test_outcome_serializes_the_acceptance_into_the_evidence_artifact():
    data = evaluate_gate("k_anonymity", measured_pass=False, acceptance=_acceptance()).to_dict()
    assert data["status"] == GATE_ACCEPTED_RISK
    assert data["measured_pass"] is False
    assert data["acceptance"]["accepted_by"] == "A. Reviewer, Privacy Office"
    assert data["acceptance"]["applies_to"] == "synthetic demo estate"


# ---------------------------------------------------------------------------------------
# Reading acceptances out of the rulebook
# ---------------------------------------------------------------------------------------
def test_absent_block_is_deny_by_default_not_an_error():
    assert load_risk_acceptance({"profiles": {}}, "k_anonymity") is None
    assert load_risk_acceptance({"privacy_gates": {"k_anonymity": {"k": 5}}}, "k_anonymity") is None


def test_malformed_block_surfaces_as_defects_rather_than_vanishing():
    """A garbled acceptance must not be silently skipped into a deny-by-default *pass*."""
    cfg = {"privacy_gates": {"k_anonymity": {"k": 5, "accepted_risk": "yes please"}}}
    acceptance = load_risk_acceptance(cfg, "k_anonymity")
    assert acceptance is not None
    assert acceptance.defects()
    assert evaluate_gate("k_anonymity", False, acceptance).status == GATE_FAIL


def test_round_trip_from_a_config_mapping():
    cfg = {
        "privacy_gates": {
            "k_anonymity": {
                "k": 5,
                "accepted_risk": {
                    "reason": GOOD_REASON,
                    "accepted_by": "A. Reviewer",
                    "applies_to": "demo",
                    "expires_utc": FUTURE,
                },
            }
        }
    }
    acceptance = load_risk_acceptance(cfg, "k_anonymity")
    assert acceptance is not None
    assert acceptance.control == "k_anonymity"
    assert acceptance.is_usable()


# ---------------------------------------------------------------------------------------
# Config validation of the privacy_gates block
# ---------------------------------------------------------------------------------------
def _cfg(gates):
    return {"active_profile": "p", "profiles": {"p": {"tables": {}}}, "privacy_gates": gates}


def test_shipped_config_privacy_gates_validate():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert validate_config(cfg) == []
    assert "privacy_gates" in cfg, "the shipped rulebook must configure its disclosure-risk gates"


@pytest.mark.parametrize("control", ["k_anonymity", "l_diversity", "t_closeness"])
def test_shipped_acceptances_are_usable_and_scoped_to_synthetic_data(control):
    """The demo ships waivers so it runs, but each must be in-date and say it is not real PHI."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    acceptance = load_risk_acceptance(cfg, control)
    assert acceptance is not None, f"{control} must ship an explicit acceptance or meet its floor"
    assert acceptance.defects() == []
    assert "synthetic" in acceptance.applies_to.lower()
    assert "not real phi" in acceptance.applies_to.lower()


def test_unknown_gate_is_rejected():
    errors = validate_config(_cfg({"j_anonymity": {"k": 5}}))
    assert any("unknown gate" in e for e in errors)


def test_missing_threshold_is_rejected():
    assert any(
        "missing required threshold" in e for e in validate_config(_cfg({"k_anonymity": {}}))
    )


@pytest.mark.parametrize(
    "gates",
    [
        {"k_anonymity": {"k": 0}},
        {"k_anonymity": {"k": "five"}},
        {"t_closeness": {"t": 1.5}},
        {"t_closeness": {"t": -0.1}},
    ],
)
def test_out_of_range_threshold_is_rejected(gates):
    assert validate_config(_cfg(gates))


def test_incomplete_acceptance_is_rejected_by_the_config_validator():
    """Caught at load time, not run time — a half-written waiver never reaches the gate."""
    errors = validate_config(
        _cfg({"k_anonymity": {"k": 5, "accepted_risk": {"reason": GOOD_REASON}}})
    )
    assert any("accepted_by" in e for e in errors)
    assert any("expires_utc" in e for e in errors)


def test_quasi_identifiers_must_be_column_names():
    assert any(
        "quasi_identifiers" in e
        for e in validate_config(_cfg({"k_anonymity": {"k": 5, "quasi_identifiers": "BirthYear"}}))
    )


# ---------------------------------------------------------------------------------------
# The remedy: a suppression cutoff that does not invent a new violation
# ---------------------------------------------------------------------------------------
def test_no_small_classes_leaves_the_cutoff_at_k():
    assert suppression_cutoff({5: 10, 9: 3}, 5) == 5


def test_large_residual_pool_is_its_own_safe_class():
    # 100 singletons collapse into one class of 100, comfortably above k.
    assert suppression_cutoff({1: 100, 7: 4}, 5) == 5


def test_tiny_residual_pool_is_widened_until_it_clears_k():
    """Blanking 2 rows would create a k=2 class of exactly the most identifiable people.

    The cutoff must rise to absorb the next-smallest classes instead of shipping that.
    """
    histogram = {1: 2, 6: 1, 20: 5}
    cutoff = suppression_cutoff(histogram, 5)
    assert cutoff == 7
    pooled = sum(size * n for size, n in histogram.items() if size < cutoff)
    assert pooled >= 5


def test_cutoff_absorbs_everything_when_the_table_is_smaller_than_k():
    assert suppression_cutoff({1: 2}, 5) == 2


def test_cutoff_rejects_a_nonsensical_k():
    with pytest.raises(ValueError):
        suppression_cutoff({1: 5}, 0)
