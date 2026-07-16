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
    "build_determination_report",
    "residual_scan_from_hits",
]


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
    notes: str | None = None

    @property
    def passes(self) -> bool:
        """True only when every supplied check passes.

        A check that was not supplied (``None``) is treated as not-applicable and does not
        block the gate — the reviewer decides which metrics are required for their dataset.
        """
        checks = [
            self.k_anonymity.passes if self.k_anonymity else True,
            self.l_diversity.passes if self.l_diversity else True,
            self.t_closeness.passes if self.t_closeness else True,
            self.residual_scan.clean if self.residual_scan else True,
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
        for obj in (self.k_anonymity, self.l_diversity, self.t_closeness, self.residual_scan):
            if obj is not None:
                lines.append(f"- {obj.summary()}")
        if not any((self.k_anonymity, self.l_diversity, self.t_closeness, self.residual_scan)):
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
