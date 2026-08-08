"""
determination.py — Expert Determination evidence pack.

Why this exists
---------------
HIPAA offers two de-identification methods: **Safe Harbor** (strip the 18 identifiers) and
**Expert Determination** (§164.514(b)(1)) — a qualified person applies statistical/scientific
principles and documents that the re-identification risk is *very small*. That determination
is only credible if it is backed by **evidence**: which rulebook was applied, what the
residual disclosure-risk metrics are, and that no direct identifiers survived.

This module assembles that evidence into a single, **PHI-free** artifact a reviewer can sign:

- the config fingerprint (which exact rulebook produced the output),
- the k-anonymity / l-diversity / t-closeness measurements over the quasi-identifiers,
- the residual direct-identifier scan result,
- an overall PASS/FAIL gate and the determination metadata (method, reviewer, review-by date).

It is deliberately **aggregate-only**: it consumes already-computed report objects and count
summaries, never row-level data, so the pack itself is safe to persist and share. Passing this
gate is *necessary but not sufficient* for a real determination — a qualified human still signs
(see docs/pre_real_phi_checklist.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .privacy_metrics import KAnonymityReport, LDiversityReport, TClosenessReport

__all__ = [
    "ResidualScanResult",
    "DeterminationReport",
    "MethodEligibility",
    "DerivedValueRule",
    "RiskAcceptance",
    "ActualKnowledgeAttestation",
    "GateOutcome",
    "SAFE_HARBOR",
    "EXPERT_DETERMINATION",
    "DERIVED_VALUE_STRATEGIES",
    "GATE_PASS",
    "GATE_ACCEPTED_RISK",
    "GATE_FAIL",
    "MIN_ACCEPTANCE_REASON_CHARS",
    "MIN_ATTESTATION_STATEMENT_CHARS",
    "assess_method_eligibility",
    "build_determination_report",
    "residual_scan_from_hits",
    "load_risk_acceptance",
    "load_actual_knowledge_attestation",
    "evaluate_gate",
]


SAFE_HARBOR = "safe_harbor"
EXPERT_DETERMINATION = "expert_determination"

# Strategies whose output is *derived from* the individual's own data rather than removed.
#
# This is the crux of the two methods. Safe Harbor (§164.514(b)(2)) is a removal standard,
# and its re-identification-code exception at §164.514(c)(1) admits a code only when it is
# "not derived from or related to information about the individual". An HMAC of an MRN is
# derived from the MRN; a per-patient date shift is derived from the patient's real dates.
# Identifier (R), "any other unique identifying number, characteristic, or code", is written
# to catch exactly this, and HHS §3.5 says a code derived from PHI "would have to be removed"
# under Safe Harbor.
#
# HHS §2.9 does permit cryptographic-hash-derived values in a de-identified release --
# but scopes the permission to the **Expert Determination** method, and only while the keys
# stay undisclosed. So these strategies do not make output unsafe; they make a *Safe Harbor
# claim* unavailable. The distinction the scorecard has to enforce is which claim you may make.
#
# ``synthesize`` is on this list because of how it is IMPLEMENTED, not because a synthetic
# value is inherently derived. :func:`deid_engine.strat_synthesize` HMACs the source value and
# indexes a name list with the digest, so the same real name always yields the same fake one --
# a consistent, pepper-keyed pseudonym derived from the individual's name. The output space is
# small enough (256 full-name combinations) that it is a poor re-identifier, but HHS §3.2 is
# explicit that even patient *initials* fail Safe Harbor, so "derived but lossy" is not a
# category the rule recognises. Swap in a generator that ignores the input value and this
# entry should come off the list.
#
# ``surrogate`` is deliberately ABSENT, and it is the one omission worth defending. It does the
# same job as ``tokenize`` -- a stable per-patient key that conforms two source systems -- but
# :func:`deid_engine.strat_surrogate` looks the value up in a mapping minted from a CSPRNG by
# :func:`crosswalk.mint_crosswalk`, so the code carries no information about the individual and
# cannot be recomputed from their data by anyone, including us. That is precisely the case
# §164.514(c) was written to allow ("may assign a code ... not derived from or related to
# information about the individual"), and it is what lets a Safe Harbor profile link across
# source systems at all. The security half of §164.514(c) is not a code property and cannot be
# checked here: it is the Vault's job to keep the mapping away from the analytics workspace.
DERIVED_VALUE_STRATEGIES = frozenset({"tokenize", "date_shift", "synthesize"})


def _derived_strategy_label(strategy: str | None, params: dict[str, Any]) -> str | None:
    """Return a label when this rule emits a value derived from the individual, else ``None``.

    The strategy name alone cannot answer this for every rule. ``redact_text`` replaces each
    detected span with either an entity *label* (``<NAME>``) or a *token* derived from the span,
    and only the second is a derived value -- so the parameters have to be read too. Checking
    the name and stopping is how a config sneaks a pseudonym past a Safe Harbor claim.
    """
    if strategy in DERIVED_VALUE_STRATEGIES:
        return strategy
    if strategy == "redact_text" and params.get("replacement") == "token":
        return "redact_text(replacement=token)"
    return None


@dataclass(frozen=True)
class DerivedValueRule:
    """One configured rule that emits a value derived from the individual."""

    table: str
    column: str
    strategy: str

    def __str__(self) -> str:
        return f"{self.table}.{self.column} ({self.strategy})"


@dataclass
class MethodEligibility:
    """Which de-identification method a given rulebook profile may actually claim.

    Computed from the config rather than declared alongside it, for the same reason the
    semantic model is generated rather than hand-maintained: a declaration can drift out of
    agreement with the rules it describes, and this one drifts silently into a false
    compliance claim.
    """

    profile: str
    claimed_method: str
    derived_rules: tuple[DerivedValueRule, ...]

    @property
    def safe_harbor_available(self) -> bool:
        """True when no rule emits a value derived from the individual."""
        return not self.derived_rules

    @property
    def claimable_method(self) -> str:
        """The strongest method this configuration actually supports."""
        return SAFE_HARBOR if self.safe_harbor_available else EXPERT_DETERMINATION

    @property
    def passes(self) -> bool:
        """True when the claimed method is supported by the configuration.

        Claiming Expert Determination over a config that would also satisfy Safe Harbor is
        fine -- it is the weaker claim. The reverse is not.
        """
        return not (self.claimed_method == SAFE_HARBOR and not self.safe_harbor_available)

    def summary(self) -> str:
        verdict = "PASS" if self.passes else "FAIL"
        if self.passes:
            detail = f"claim '{self.claimed_method}' is supported by profile '{self.profile}'"
            if self.derived_rules:
                detail += f" ({len(self.derived_rules)} derived-value rule(s))"
        else:
            shown = ", ".join(str(r) for r in self.derived_rules[:3])
            more = f" (+{len(self.derived_rules) - 3} more)" if len(self.derived_rules) > 3 else ""
            detail = (
                f"profile '{self.profile}' claims Safe Harbor but emits "
                f"{len(self.derived_rules)} value(s) derived from the individual: "
                f"{shown}{more}. Per HHS \u00a72.9 these require Expert Determination"
            )
        return f"[method-eligibility {verdict}] {detail}"


def assess_method_eligibility(
    cfg: dict[str, Any],
    *,
    claimed_method: str,
    profile: str | None = None,
) -> MethodEligibility:
    """Check a claimed de-identification method against what the rulebook actually does.

    Parameters
    ----------
    cfg:
        The loaded ``deid_rules.yaml``.
    claimed_method:
        The method the operator intends to assert, e.g. ``"safe_harbor"``. This is a human
        claim on purpose -- the point of the check is to test an assertion, not to derive one
        and then agree with itself.
    profile:
        Profile to inspect. Defaults to the config's ``active_profile``.
    """
    profile = profile or cfg.get("active_profile", "")
    tables = cfg.get("profiles", {}).get(profile, {}).get("tables", {}) or {}

    derived: list[DerivedValueRule] = []
    for table, columns in tables.items():
        for column, rule in (columns or {}).items():
            strategy: str | None
            params: dict[str, Any]
            if isinstance(rule, str):
                strategy, params = rule, {}
            else:
                rule = rule or {}
                strategy = rule.get("strategy")
                params = {k: v for k, v in rule.items() if k != "strategy"}
            label = _derived_strategy_label(strategy, params)
            if label is not None:
                derived.append(DerivedValueRule(table=table, column=column, strategy=label))

    return MethodEligibility(
        profile=profile,
        claimed_method=claimed_method,
        derived_rules=tuple(derived),
    )


# --------------------------------------------------------------------------------------
# Recorded risk acceptance — the only way a failing disclosure-risk gate goes green
# --------------------------------------------------------------------------------------
GATE_PASS = "PASS"  # noqa: S105 - a gate verdict, not a credential
GATE_ACCEPTED_RISK = "ACCEPTED_RISK"
GATE_FAIL = "FAIL"

# A reason short enough to be a shrug is not an audit record. This floor is arbitrary in its
# exact value and deliberate in its existence: it stops "ok", "n/a" and "demo" from buying a
# pass on a re-identification control.
MIN_ACCEPTANCE_REASON_CHARS = 20

# Substrings that mean the template was never filled in. An unedited placeholder must not be
# able to sign off on residual re-identification risk.
_PLACEHOLDER_MARKERS = ("todo", "fixme", "changeme", "change me", "xxx", "<", "tbd")


def _placeholder_defect(field_name: str, value: Any) -> str | None:
    """Return a defect string when ``value`` is missing, blank or an unedited placeholder."""
    if not isinstance(value, str) or not value.strip():
        return f"{field_name} is missing or blank"
    lowered = value.strip().lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            return f"{field_name} still contains the placeholder text {marker!r}"
    return None


@dataclass(frozen=True)
class RiskAcceptance:
    """A named human's time-limited, written acceptance of a failing disclosure-risk control.

    Why this type exists
    --------------------
    Some disclosure-risk controls cannot be satisfied by the data you actually have. A
    patient-grain table that publishes birth year and 3-digit ZIP will not reach k>=5 on any
    realistic population — the quasi-identifier domain is simply larger than the cohort. The
    honest options are to generalize further, to suppress the tail, or to *accept the residual
    risk*. The third option is legitimate; accepting it silently is not.

    An advisory check and an accepted risk look identical in a green run and are completely
    different in an audit. This dataclass forces the difference into the evidence artifact: a
    reason, a person, a scope, and an expiry date. When the acceptance lapses or the placeholder
    was never edited, the gate fails closed.
    """

    control: str
    reason: str
    accepted_by: str
    expires_utc: str
    applies_to: str

    def is_expired(self, as_of: datetime | None = None) -> bool:
        """True when the acceptance has lapsed. An unparseable date counts as expired."""
        as_of = as_of or datetime.now(UTC)
        try:
            expires = datetime.fromisoformat(self.expires_utc)
        except (TypeError, ValueError):
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return as_of >= expires

    def defects(self, as_of: datetime | None = None) -> list[str]:
        """Every reason this acceptance cannot be relied on (empty list == usable)."""
        problems: list[str] = []
        for name, value in (
            ("reason", self.reason),
            ("accepted_by", self.accepted_by),
            ("applies_to", self.applies_to),
            ("expires_utc", self.expires_utc),
        ):
            defect = _placeholder_defect(name, value)
            if defect:
                problems.append(defect)

        if isinstance(self.reason, str) and len(self.reason.strip()) < MIN_ACCEPTANCE_REASON_CHARS:
            problems.append(
                f"reason is shorter than {MIN_ACCEPTANCE_REASON_CHARS} characters — "
                "state what was accepted and why"
            )
        if self.is_expired(as_of):
            problems.append(f"acceptance expired on {self.expires_utc}")
        return problems

    def is_usable(self, as_of: datetime | None = None) -> bool:
        return not self.defects(as_of)

    def summary(self) -> str:
        verdict = "usable" if self.is_usable() else "UNUSABLE"
        return (
            f"[risk-acceptance {verdict}] {self.control}: accepted by {self.accepted_by!r} "
            f"for {self.applies_to!r} until {self.expires_utc}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "reason": self.reason,
            "accepted_by": self.accepted_by,
            "applies_to": self.applies_to,
            "expires_utc": self.expires_utc,
            "expired": self.is_expired(),
            "usable": self.is_usable(),
            "defects": self.defects(),
        }


def load_risk_acceptance(cfg: Any, control: str) -> RiskAcceptance | None:
    """Read ``privacy_gates.<control>.accepted_risk`` out of a loaded config.

    Returns ``None`` when no acceptance is recorded — which is the deny-by-default case, not an
    error. A malformed block is returned as a ``RiskAcceptance`` with blank fields rather than
    silently ignored, so ``defects()`` reports it instead of it vanishing into a pass.
    """
    if not isinstance(cfg, dict):
        return None
    gates = cfg.get("privacy_gates")
    if not isinstance(gates, dict):
        return None
    gate = gates.get(control)
    if not isinstance(gate, dict):
        return None
    accepted = gate.get("accepted_risk")
    if accepted is None:
        return None
    if not isinstance(accepted, dict):
        accepted = {}
    return RiskAcceptance(
        control=control,
        reason=str(accepted.get("reason", "") or ""),
        accepted_by=str(accepted.get("accepted_by", "") or ""),
        expires_utc=str(accepted.get("expires_utc", "") or ""),
        applies_to=str(accepted.get("applies_to", "") or ""),
    )


# --------------------------------------------------------------------------------------
# Safe Harbor prong (ii) — §164.514(b)(2)(ii), actual knowledge
# --------------------------------------------------------------------------------------
# Safe Harbor has TWO conditions, and almost every implementation ships only the first.
#
#   (i)  remove the 18 enumerated identifiers, AND
#   (ii) "the covered entity does not have actual knowledge that the information could be
#        used alone or in combination with other information to identify an individual who
#        is a subject of the information."
#
# Prong (ii) is not a data property. No scan can establish it, because it is a statement
# about what the covered entity *knows* — that the only oncologist in a 3-digit ZIP is
# identifiable from specialty alone, that a dataset was already published with a joinable
# key, that a rare diagnosis code names one person. Software cannot see any of that.
#
# What software CAN do is refuse to let the claim be made silently. Deleting eighteen
# columns is the part that automates; knowing your own data is the part that does not, and
# a pipeline that reports "Safe Harbor: PASS" while covering only prong (i) is overstating
# its own evidence. So this ships UNSIGNED and fails closed: a human puts their name to it,
# scoped and time-limited, or the scorecard does not certify Safe Harbor.
MIN_ATTESTATION_STATEMENT_CHARS = 80


@dataclass(frozen=True)
class ActualKnowledgeAttestation:
    """A named human's time-limited attestation to §164.514(b)(2)(ii).

    Deliberately shaped like :class:`RiskAcceptance`: a statement, a person, a scope, an
    expiry, and the same placeholder detection. The two are the only places in this
    accelerator where a human judgement outranks a measurement, and they should look and
    fail alike.
    """

    statement: str
    attested_by: str
    role: str
    applies_to: str
    attested_utc: str
    expires_utc: str
    residual_risks: tuple[str, ...] = ()

    def is_expired(self, as_of: datetime | None = None) -> bool:
        """True when the attestation has lapsed. An unparseable date counts as expired.

        Expiry is not bureaucracy. Actual knowledge is a statement about a moment: the
        estate acquires new datasets, publishes new extracts, and a judgement that was
        sound in March can be wrong by September without anything in *this* pipeline
        changing. An attestation with no end date is a claim that nothing will ever be
        learned again.
        """
        as_of = as_of or datetime.now(UTC)
        try:
            expires = datetime.fromisoformat(self.expires_utc)
        except (TypeError, ValueError):
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return as_of >= expires

    def defects(self, as_of: datetime | None = None) -> list[str]:
        """Every reason this attestation cannot be relied on (empty list == usable)."""
        problems: list[str] = []
        for name, value in (
            ("statement", self.statement),
            ("attested_by", self.attested_by),
            ("role", self.role),
            ("applies_to", self.applies_to),
            ("expires_utc", self.expires_utc),
        ):
            defect = _placeholder_defect(name, value)
            if defect:
                problems.append(defect)

        if (
            isinstance(self.statement, str)
            and len(self.statement.strip()) < MIN_ATTESTATION_STATEMENT_CHARS
        ):
            problems.append(
                f"statement is shorter than {MIN_ATTESTATION_STATEMENT_CHARS} characters — "
                "name the datasets considered and the residual risks reviewed"
            )
        if self.is_expired(as_of):
            problems.append(f"attestation expired on {self.expires_utc}")
        return problems

    def is_usable(self, as_of: datetime | None = None) -> bool:
        return not self.defects(as_of)

    def summary(self) -> str:
        verdict = "usable" if self.is_usable() else "UNSIGNED/UNUSABLE"
        return (
            f"[actual-knowledge {verdict}] attested by {self.attested_by!r} ({self.role}) "
            f"for {self.applies_to!r} until {self.expires_utc}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "attested_by": self.attested_by,
            "role": self.role,
            "applies_to": self.applies_to,
            "attested_utc": self.attested_utc,
            "expires_utc": self.expires_utc,
            "residual_risks": list(self.residual_risks),
            "expired": self.is_expired(),
            "usable": self.is_usable(),
            "defects": self.defects(),
        }


def load_actual_knowledge_attestation(cfg: Any) -> ActualKnowledgeAttestation | None:
    """Read the top-level ``actual_knowledge`` block out of a loaded config.

    Returns ``None`` only when the block is absent entirely — which the scorecard must
    treat as a FAIL for a Safe Harbor claim, not as "nothing to check". A malformed block
    comes back with blank fields so ``defects()`` reports it rather than it vanishing.
    """
    if not isinstance(cfg, dict):
        return None
    block = cfg.get("actual_knowledge")
    if block is None:
        return None
    if not isinstance(block, dict):
        block = {}
    risks = block.get("residual_risks") or ()
    if isinstance(risks, str):
        risks = (risks,)
    return ActualKnowledgeAttestation(
        statement=str(block.get("statement", "") or ""),
        attested_by=str(block.get("attested_by", "") or ""),
        role=str(block.get("role", "") or ""),
        applies_to=str(block.get("applies_to", "") or ""),
        attested_utc=str(block.get("attested_utc", "") or ""),
        expires_utc=str(block.get("expires_utc", "") or ""),
        residual_risks=tuple(str(r) for r in risks),
    )


@dataclass
class GateOutcome:
    """The three-state result of a disclosure-risk control: PASS / ACCEPTED_RISK / FAIL."""

    control: str
    measured_pass: bool
    detail: str = ""
    acceptance: RiskAcceptance | None = None
    acceptance_defects: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if self.measured_pass:
            return GATE_PASS
        if self.acceptance is not None and not self.acceptance_defects:
            return GATE_ACCEPTED_RISK
        return GATE_FAIL

    @property
    def passes(self) -> bool:
        """True when the gate does not block the run (measured pass OR a valid acceptance)."""
        return self.status != GATE_FAIL

    def summary(self) -> str:
        if self.status == GATE_PASS:
            return f"[{self.control} PASS] {self.detail}".rstrip()
        if self.status == GATE_ACCEPTED_RISK:
            assert self.acceptance is not None  # noqa: S101 - narrowed by status
            return (
                f"[{self.control} ACCEPTED_RISK] {self.detail} — accepted by "
                f"{self.acceptance.accepted_by!r} for {self.acceptance.applies_to!r} "
                f"until {self.acceptance.expires_utc}: {self.acceptance.reason}"
            )
        if self.acceptance_defects:
            return (
                f"[{self.control} FAIL] {self.detail} — a risk acceptance is recorded but "
                f"cannot be relied on: {'; '.join(self.acceptance_defects)}"
            )
        return (
            f"[{self.control} FAIL] {self.detail} — no risk acceptance recorded. Generalize "
            f"further, suppress the small classes, or record privacy_gates.{self.control}."
            "accepted_risk with a reason, an owner, a scope and an expiry."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "status": self.status,
            "measured_pass": self.measured_pass,
            "passes": self.passes,
            "detail": self.detail,
            "summary": self.summary(),
            "acceptance": self.acceptance.to_dict() if self.acceptance else None,
            "acceptance_defects": list(self.acceptance_defects),
        }


def evaluate_gate(
    control: str,
    measured_pass: bool,
    acceptance: RiskAcceptance | None = None,
    *,
    detail: str = "",
    as_of: datetime | None = None,
) -> GateOutcome:
    """Combine a measured control result with any recorded acceptance into a gate outcome.

    Deny-by-default: a measurement that fails with no usable acceptance is a ``FAIL`` that
    blocks the run. This is the difference between a control and a statistic.
    """
    defects: tuple[str, ...] = ()
    if acceptance is not None and not measured_pass:
        defects = tuple(acceptance.defects(as_of))
    return GateOutcome(
        control=control,
        measured_pass=measured_pass,
        detail=detail,
        acceptance=acceptance,
        acceptance_defects=defects,
    )


@dataclass
class ResidualScanResult:
    """Outcome of a residual direct-identifier scan over de-identified output.

    ``pattern_hits`` maps pattern name (e.g. ``"ssn"``) -> hit count. Counts only — never the
    matched values. ``clean`` is True when no pattern matched anywhere.
    """

    tables_scanned: int
    rows_scanned: int
    pattern_hits: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.pattern_hits

    def summary(self) -> str:
        verdict = "PASS" if self.clean else "FAIL"
        detail = "no residual identifiers" if self.clean else f"hits={self.pattern_hits}"
        return (
            f"[residual-scan {verdict}] {self.rows_scanned} rows across "
            f"{self.tables_scanned} tables: {detail}"
        )


@dataclass
class DeterminationReport:
    """PHI-free evidence pack backing a HIPAA §164.514(b) expert determination.

    Bundles the config fingerprint, disclosure-risk measurements, and residual-identifier
    scan into one artifact with a single ``passes`` gate. Serializes to JSON/markdown for
    the reviewer's record.
    """

    generated_utc: str
    method: str
    config_sha256: str
    engine_version: str
    reviewer: str | None
    review_by_utc: str | None
    k_anonymity: KAnonymityReport | None
    l_diversity: LDiversityReport | None
    t_closeness: TClosenessReport | None
    residual_scan: ResidualScanResult | None
    method_eligibility: MethodEligibility | None = None
    notes: str | None = None

    @property
    def passes(self) -> bool:
        """True only when every supplied check passes.

        A check that was not supplied (``None``) is treated as not-applicable and does not
        block the gate — the reviewer decides which metrics are required for their dataset.
        The one exception in spirit is ``method_eligibility``: when it *is* supplied it can
        fail the whole pack on its own, because a pack that asserts an unavailable method is
        not weak evidence, it is wrong evidence.
        """
        checks = [
            self.k_anonymity.passes if self.k_anonymity else True,
            self.l_diversity.passes if self.l_diversity else True,
            self.t_closeness.passes if self.t_closeness else True,
            self.residual_scan.clean if self.residual_scan else True,
            self.method_eligibility.passes if self.method_eligibility else True,
        ]
        return all(checks)

    def is_review_expired(self, as_of: datetime | None = None) -> bool | None:
        """Return True/False if a review-by date is set (None if not).

        A determination is time-limited: it is valid only for the data and re-identification
        landscape assessed at sign-off. A naive expiry timestamp is interpreted as UTC.
        """
        if not self.review_by_utc:
            return None
        as_of = as_of or datetime.now(UTC)
        expires = datetime.fromisoformat(self.review_by_utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return as_of >= expires

    def to_dict(self) -> dict[str, Any]:
        def report(obj: Any) -> dict[str, Any] | None:
            if obj is None:
                return None
            # Report dataclasses expose a summary() + passes/clean; capture both the verdict
            # and a compact metric snapshot without importing dataclasses.asdict on nested
            # tuples that json can't render.
            data: dict[str, Any] = {"summary": obj.summary()}
            for attr in (
                "k",
                "l",
                "t",
                "threshold",
                "num_classes",
                "violating_classes",
                "violating_records",
                "num_records",
                "quasi_identifiers",
                "sensitive_attribute",
            ):
                if hasattr(obj, attr):
                    data[attr] = getattr(obj, attr)
            if hasattr(obj, "passes"):
                data["passes"] = obj.passes
            return data

        return {
            "generated_utc": self.generated_utc,
            "method": self.method,
            "passes": self.passes,
            "config_sha256": self.config_sha256,
            "engine_version": self.engine_version,
            "reviewer": self.reviewer,
            "review_by_utc": self.review_by_utc,
            "review_expired": self.is_review_expired(),
            "k_anonymity": report(self.k_anonymity),
            "l_diversity": report(self.l_diversity),
            "t_closeness": report(self.t_closeness),
            "residual_scan": (
                {
                    "summary": self.residual_scan.summary(),
                    "clean": self.residual_scan.clean,
                    "tables_scanned": self.residual_scan.tables_scanned,
                    "rows_scanned": self.residual_scan.rows_scanned,
                    "pattern_hits": self.residual_scan.pattern_hits,
                }
                if self.residual_scan
                else None
            ),
            "method_eligibility": (
                {
                    "summary": self.method_eligibility.summary(),
                    "passes": self.method_eligibility.passes,
                    "profile": self.method_eligibility.profile,
                    "claimed_method": self.method_eligibility.claimed_method,
                    "claimable_method": self.method_eligibility.claimable_method,
                    "safe_harbor_available": self.method_eligibility.safe_harbor_available,
                    "derived_value_rules": [str(r) for r in self.method_eligibility.derived_rules],
                }
                if self.method_eligibility
                else None
            ),
            "notes": self.notes,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def to_markdown(self) -> str:
        """Render a human-readable determination record for the reviewer's file."""
        verdict = "✅ PASS" if self.passes else "❌ FAIL"
        lines = [
            "# Expert Determination Evidence Pack",
            "",
            f"- **Overall gate:** {verdict}",
            f"- **Method:** {self.method}",
            f"- **Generated (UTC):** {self.generated_utc}",
            f"- **Engine version:** {self.engine_version}",
            f"- **Config SHA-256:** `{self.config_sha256}`",
            f"- **Reviewer:** {self.reviewer or '_unsigned_'}",
            f"- **Review by (UTC):** {self.review_by_utc or '_not set_'}",
        ]
        expired = self.is_review_expired()
        if expired is not None:
            lines.append(f"- **Review expired:** {'YES' if expired else 'no'}")
        lines += ["", "## Checks", ""]
        for obj in (
            self.method_eligibility,
            self.k_anonymity,
            self.l_diversity,
            self.t_closeness,
            self.residual_scan,
        ):
            if obj is not None:
                lines.append(f"- {obj.summary()}")
        if not any(
            (
                self.method_eligibility,
                self.k_anonymity,
                self.l_diversity,
                self.t_closeness,
                self.residual_scan,
            )
        ):
            lines.append("- _no checks supplied_")
        if self.notes:
            lines += ["", "## Notes", "", self.notes]
        lines += [
            "",
            "> Passing this gate is necessary but **not sufficient** for a HIPAA "
            "§164.514(b) determination. A qualified reviewer must sign off. "
            "See docs/pre_real_phi_checklist.md.",
        ]
        return "\n".join(lines)


def build_determination_report(
    *,
    method: str,
    config_sha256: str,
    engine_version: str,
    k_anonymity: KAnonymityReport | None = None,
    l_diversity: LDiversityReport | None = None,
    t_closeness: TClosenessReport | None = None,
    residual_scan: ResidualScanResult | None = None,
    method_eligibility: MethodEligibility | None = None,
    reviewer: str | None = None,
    review_by_utc: str | None = None,
    notes: str | None = None,
) -> DeterminationReport:
    """Assemble a :class:`DeterminationReport` from already-computed evidence.

    Parameters
    ----------
    method : str
        The claimed de-identification method, e.g. ``"expert_determination"`` or
        ``"safe_harbor"``.
    config_sha256 : str
        Fingerprint of the rulebook that produced the output (``audit.config_fingerprint``),
        so the determination is bound to a specific, reproducible config.
    k_anonymity, l_diversity, t_closeness : report objects, optional
        Disclosure-risk measurements from ``privacy_metrics``. Supply the ones relevant to
        your dataset; omitted metrics are treated as not-applicable.
    residual_scan : ResidualScanResult, optional
        Aggregate outcome of a residual direct-identifier scan over the de-identified output.
    method_eligibility : MethodEligibility, optional
        Result of :func:`assess_method_eligibility` — whether ``method`` is a claim the
        rulebook actually supports. Supply this whenever you assert a method publicly.
    reviewer, review_by_utc : str, optional
        The qualified reviewer and the (time-limited) review-by date.
    """
    return DeterminationReport(
        generated_utc=datetime.now(UTC).isoformat(),
        method=method,
        config_sha256=config_sha256,
        engine_version=engine_version,
        reviewer=reviewer,
        review_by_utc=review_by_utc,
        k_anonymity=k_anonymity,
        l_diversity=l_diversity,
        t_closeness=t_closeness,
        residual_scan=residual_scan,
        method_eligibility=method_eligibility,
        notes=notes,
    )


def residual_scan_from_hits(
    pattern_hits: dict[str, int],
    *,
    tables_scanned: int,
    rows_scanned: int,
) -> ResidualScanResult:
    """Convenience: wrap a ``{pattern: count}`` dict (e.g. from ``validation.scan_*``).

    Zero-count entries are dropped so ``clean`` reflects only real hits.
    """
    hits = {name: count for name, count in pattern_hits.items() if count}
    return ResidualScanResult(
        tables_scanned=tables_scanned,
        rows_scanned=rows_scanned,
        pattern_hits=hits,
    )
