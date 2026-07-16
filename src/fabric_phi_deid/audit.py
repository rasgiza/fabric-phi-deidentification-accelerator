"""
audit.py — provenance, run manifests, and PHI-safe structured logging.

A de-identification run is a compliance-relevant event. This module produces a **run
manifest**: a tamper-evident record of *what rulebook* was applied to *which tables* by
*whom*, when — using only **metadata and counts**. It deliberately records NO data values,
so a manifest can be persisted and shared without itself becoming a PHI artifact.

Key properties
--------------
- ``config_fingerprint`` : a SHA-256 of the canonicalized config, so you can prove which
  version of the rulebook produced a given output (and detect drift between runs).
- ``build_run_manifest`` : per-table strategy counts derived from the resolved *plan*
  (never from the data), plus optional row counts the caller supplies.
- ``get_audit_logger``   : a stdlib logger preconfigured for audit lines. Callers MUST NOT
  pass PHI to it; helper docstrings and the manifest shape steer usage toward counts only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .deid_engine import resolve_table_plan

__all__ = [
    "config_fingerprint",
    "TableManifest",
    "RunManifest",
    "summarize_table_plan",
    "build_run_manifest",
    "write_manifest",
    "get_audit_logger",
]


def config_fingerprint(cfg: dict) -> str:
    """Return a stable SHA-256 hex digest of the config.

    Canonicalizes via ``json.dumps(sort_keys=True)`` so key ordering / formatting does not
    change the fingerprint — only the *meaning* of the rulebook does.
    """
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summarize_table_plan(cfg: dict, profile: str, table: str, columns: list[str]) -> dict:
    """Return per-strategy column counts + the classified column lists for a table.

    Metadata only (column names + strategy names). No data values are read.
    """
    plan = resolve_table_plan(cfg, profile, table, columns)
    counts: Counter[str] = Counter(strategy for strategy, _ in plan.values())
    by_strategy: dict[str, list[str]] = {}
    for column, (strategy, _params) in plan.items():
        by_strategy.setdefault(strategy, []).append(column)
    return {
        "counts": dict(counts),
        "columns_by_strategy": {k: sorted(v) for k, v in by_strategy.items()},
    }


@dataclass
class TableManifest:
    """Per-table record within a run manifest. Counts/metadata only — never data."""

    table: str
    strategy_counts: dict[str, int] = field(default_factory=dict)
    columns_by_strategy: dict[str, list[str]] = field(default_factory=dict)
    input_rows: int | None = None
    output_rows: int | None = None


@dataclass
class RunManifest:
    """Tamper-evident record of a single de-identification run. PHI-free by construction."""

    run_id: str
    timestamp_utc: str
    profile: str
    config_sha256: str
    engine_version: str
    actor: str
    pepper_key_version: str | None = None
    # Time-limited determination metadata. An Expert Determination is valid only for the data,
    # environment, and re-identification landscape assessed at sign-off; HHS guidance treats it
    # as expiring. Recording the method, review-by date, and reviewer makes a run auditably
    # traceable to a determination and flags when that determination is stale.
    determination_method: str | None = None
    determination_expires_utc: str | None = None
    determination_reviewer: str | None = None
    tables: list[TableManifest] = field(default_factory=list)

    def is_determination_expired(self, as_of: datetime | None = None) -> bool | None:
        """Return True/False if a review-by date is set (None if not).

        Compares ``determination_expires_utc`` (ISO-8601) against ``as_of`` (defaults to now,
        UTC). A naive expiry timestamp is interpreted as UTC.
        """
        if not self.determination_expires_utc:
            return None
        as_of = as_of or datetime.now(UTC)
        expires = datetime.fromisoformat(self.determination_expires_utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return as_of >= expires

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def build_run_manifest(
    cfg: dict,
    profile: str,
    actor: str,
    tables: dict[str, dict],
    *,
    engine_version: str | None = None,
    pepper_key_version: str | None = None,
    determination_method: str | None = None,
    determination_expires_utc: str | None = None,
    determination_reviewer: str | None = None,
) -> RunManifest:
    """Assemble a :class:`RunManifest`.

    Parameters
    ----------
    tables : dict[str, dict]
        Maps table name -> ``{"columns": [...], "input_rows": int?, "output_rows": int?}``.
        ``columns`` is required (the table schema); row counts are optional.
    determination_method : str, optional
        The de-identification method claimed for this run (e.g. ``"safe_harbor"`` or
        ``"expert_determination"``).
    determination_expires_utc : str, optional
        ISO-8601 review-by date for a (time-limited) determination. Surfaces via
        :meth:`RunManifest.is_determination_expired`.
    determination_reviewer : str, optional
        Name/role of the qualified reviewer who signed the determination.
    """
    if engine_version is None:
        from . import __version__

        engine_version = __version__

    table_manifests: list[TableManifest] = []
    for table, info in tables.items():
        columns = info.get("columns", [])
        summary = summarize_table_plan(cfg, profile, table, columns)
        table_manifests.append(
            TableManifest(
                table=table,
                strategy_counts=summary["counts"],
                columns_by_strategy=summary["columns_by_strategy"],
                input_rows=info.get("input_rows"),
                output_rows=info.get("output_rows"),
            )
        )

    return RunManifest(
        run_id=str(uuid.uuid4()),
        timestamp_utc=datetime.now(UTC).isoformat(),
        profile=profile,
        config_sha256=config_fingerprint(cfg),
        engine_version=engine_version,
        actor=actor,
        pepper_key_version=pepper_key_version,
        determination_method=determination_method,
        determination_expires_utc=determination_expires_utc,
        determination_reviewer=determination_reviewer,
        tables=table_manifests,
    )


def write_manifest(manifest: RunManifest, path: str) -> None:
    """Persist a run manifest as JSON (UTF-8)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(manifest.to_json())


def get_audit_logger(name: str = "fabric_phi_deid.audit") -> logging.Logger:
    """Return a stdlib logger configured for PHI-safe audit lines.

    Emits ISO-timestamped records to stdout at INFO. Callers MUST log only metadata
    (run ids, table names, counts, config fingerprints) — never data values, never the
    pepper.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)sZ [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
