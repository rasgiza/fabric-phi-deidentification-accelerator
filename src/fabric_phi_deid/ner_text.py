"""
ner_text.py — free-text PHI detection & redaction for unstructured columns.

The structured engine (``deid_engine``) classifies whole columns, but PHI also hides *inside*
free-text columns — clinical notes, comments, reason-for-visit. HIPAA requires identifiers be
removed regardless of where they appear, so those identifiers must be found and removed from
the text itself. This module does that.

Two backends, chosen automatically
-----------------------------------
- **presidio** — if ``presidio-analyzer`` is installed (``pip install 'fabric-phi-deid[nlp]'``),
  it provides named-entity recognition (PERSON, LOCATION, DATE_TIME, US_SSN, PHONE_NUMBER,
  EMAIL_ADDRESS, MEDICAL_LICENSE, ...). This is the recommended path — names/locations cannot be
  found by regex alone.
- **regex** — a dependency-free fallback covering the structured identifiers that regex *can*
  match reliably (SSN, phone, email). It is deliberately conservative and will NOT catch
  free-form names; a ``[regex-fallback]`` posture should be treated as detection-incomplete.

Everything is a pure function of its inputs plus a lazily-built, per-process analyzer singleton
(so Presidio's heavy model load happens once per Spark worker, not once per row). Spark wrappers
prefer a vectorized ``pandas_udf`` for throughput and fall back to a row UDF when Arrow/pandas is
unavailable.

PHI-safety: findings carry offsets + entity types + scores, never the matched substring, unless
the caller explicitly passes ``include_text=True`` (off by default).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .tokenization import tokenize

__all__ = [
    "NER_AVAILABLE",
    "DEFAULT_ENTITIES",
    "TextFinding",
    "analyze_text",
    "redact_text",
    "scan_texts",
    "scan_text_column",
    "redact_text_column",
    "reset_analyzer_cache",
]


# Detect Presidio once at import; keep the import itself lazy inside builders.
try:  # pragma: no cover - availability depends on the environment
    import presidio_analyzer  # noqa: F401

    NER_AVAILABLE = True
except Exception:  # pragma: no cover
    NER_AVAILABLE = False


# Entity types requested by default. These map to Presidio recognizers; the regex fallback
# implements the subset it can match reliably.
DEFAULT_ENTITIES: tuple[str, ...] = (
    "PERSON",
    "US_SSN",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "DATE_TIME",
    "LOCATION",
    "MEDICAL_LICENSE",
    "US_DRIVER_LICENSE",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "URL",
)

# Regex fallback recognizers (structured identifiers only). Names/locations are intentionally
# absent — regex cannot find them without unacceptable false positives.
_FALLBACK_PATTERNS: dict[str, re.Pattern[str]] = {
    "US_SSN": re.compile(r"\b(?!000|666|9\d\d)\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"),
    "PHONE_NUMBER": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "EMAIL_ADDRESS": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "URL": re.compile(r"\bhttps?://[^\s]+\b"),
}


@dataclass(frozen=True)
class TextFinding:
    """A single detected entity span. ``text`` is populated only when explicitly requested."""

    entity_type: str
    start: int
    end: int
    score: float
    text: str | None = None


# --------------------------------------------------------------------------------------
# Analyzer singleton (built once per process / Spark worker)
# --------------------------------------------------------------------------------------
_ANALYZER: Any | None = None
_ANALYZER_FAILED = False


def reset_analyzer_cache() -> None:
    """Clear the cached Presidio analyzer (test hook; rarely needed in production)."""
    global _ANALYZER, _ANALYZER_FAILED
    _ANALYZER = None
    _ANALYZER_FAILED = False


def _get_analyzer():
    """Return a cached Presidio ``AnalyzerEngine`` or ``None`` if Presidio is unavailable.

    The engine (and its spaCy pipeline) is expensive to construct, so it is built at most once
    per process and reused for every subsequent call — the key to acceptable Spark throughput.
    """
    global _ANALYZER, _ANALYZER_FAILED
    if _ANALYZER is not None or _ANALYZER_FAILED:
        return _ANALYZER
    if not NER_AVAILABLE:
        _ANALYZER_FAILED = True
        return None
    try:  # pragma: no cover - exercised only where Presidio + a model are installed
        from presidio_analyzer import AnalyzerEngine

        _ANALYZER = AnalyzerEngine()
    except Exception:  # pragma: no cover
        _ANALYZER_FAILED = True
        _ANALYZER = None
    return _ANALYZER


def _analyze_regex(text: str, entities: Sequence[str], score_threshold: float) -> list[TextFinding]:
    findings: list[TextFinding] = []
    wanted = set(entities)
    for name, pattern in _FALLBACK_PATTERNS.items():
        if name not in wanted:
            continue
        for m in pattern.finditer(text):
            findings.append(TextFinding(entity_type=name, start=m.start(), end=m.end(), score=1.0))
    # A fixed confidence of 1.0 for deterministic regex hits; threshold kept for API symmetry.
    return [f for f in findings if f.score >= score_threshold]


def analyze_text(
    text: str | None,
    entities: Sequence[str] | None = None,
    *,
    language: str = "en",
    score_threshold: float = 0.35,
    backend: str = "auto",
    include_text: bool = False,
) -> list[TextFinding]:
    """Detect identifier spans in ``text``. Returns findings sorted by start offset.

    Parameters
    ----------
    entities : sequence of str, optional
        Entity types to look for; defaults to :data:`DEFAULT_ENTITIES`.
    backend : {"auto", "presidio", "regex"}
        ``auto`` uses Presidio when available and falls back to regex otherwise. ``regex`` forces
        the dependency-free path (used in tests for determinism); ``presidio`` forces the model
        path and raises if it is unavailable.
    include_text : bool
        When True, each finding carries the matched substring. Off by default so findings stay
        PHI-free and safe to log/aggregate.
    """
    if not text:
        return []
    entities = list(entities) if entities is not None else list(DEFAULT_ENTITIES)

    if backend not in ("auto", "presidio", "regex"):
        raise ValueError(f"backend must be 'auto', 'presidio', or 'regex', got {backend!r}.")

    analyzer = None
    if backend in ("auto", "presidio"):
        analyzer = _get_analyzer()
        if analyzer is None and backend == "presidio":
            raise RuntimeError(
                "backend='presidio' requested but Presidio is unavailable. Install with "
                "pip install 'fabric-phi-deid[nlp]'."
            )

    if analyzer is not None:  # pragma: no cover - requires Presidio + model installed
        results = analyzer.analyze(
            text=text,
            entities=entities,
            language=language,
            score_threshold=score_threshold,
        )
        findings = [
            TextFinding(entity_type=r.entity_type, start=r.start, end=r.end, score=float(r.score))
            for r in results
        ]
    else:
        findings = _analyze_regex(text, entities, score_threshold)

    if include_text:
        findings = [
            TextFinding(f.entity_type, f.start, f.end, f.score, text[f.start : f.end])
            for f in findings
        ]
    findings.sort(key=lambda f: (f.start, f.end))
    return findings


def _merge_spans(findings: Sequence[TextFinding]) -> list[TextFinding]:
    """Merge overlapping spans, keeping the highest-scoring entity label for the merged span."""
    if not findings:
        return []
    ordered = sorted(findings, key=lambda f: (f.start, -f.end))
    merged: list[TextFinding] = [ordered[0]]
    for f in ordered[1:]:
        last = merged[-1]
        if f.start < last.end:  # overlap
            if f.end > last.end or f.score > last.score:
                keep_type = last.entity_type if last.score >= f.score else f.entity_type
                keep_score = max(last.score, f.score)
                merged[-1] = TextFinding(keep_type, last.start, max(last.end, f.end), keep_score)
        else:
            merged.append(f)
    return merged


def redact_text(
    text: str | None,
    *,
    findings: Sequence[TextFinding] | None = None,
    entities: Sequence[str] | None = None,
    replacement: str = "label",
    pepper: str | None = None,
    namespace: str = "free_text",
    score_threshold: float = 0.35,
    backend: str = "auto",
) -> str | None:
    """Return ``text`` with detected identifier spans removed/replaced.

    ``replacement`` selects the substitution strategy:

    - ``"label"``  (default) — replace each span with ``[ENTITY_TYPE]`` (e.g. ``[PERSON]``).
    - ``"token"``  — replace each span with a deterministic HMAC token of the matched substring
      (requires ``pepper``), so the same name maps to the same token everywhere — preserving
      linkage while removing the clear value.
    - ``"remove"`` — delete the span entirely.

    Overlapping spans are merged first; replacements are applied right-to-left so earlier offsets
    stay valid. Detection is delegated to :func:`analyze_text` when ``findings`` is not supplied.
    """
    if not text:
        return text
    if replacement not in ("label", "token", "remove"):
        raise ValueError(f"replacement must be 'label', 'token', or 'remove', got {replacement!r}.")
    if replacement == "token" and not pepper:
        raise ValueError("replacement='token' requires a non-empty pepper.")

    if findings is None:
        findings = analyze_text(
            text,
            entities,
            score_threshold=score_threshold,
            backend=backend,
        )
    spans = _merge_spans(findings)
    if not spans:
        return text

    out = text
    for f in sorted(spans, key=lambda s: s.start, reverse=True):
        original = out[f.start : f.end]
        if replacement == "label":
            repl = f"[{f.entity_type}]"
        elif replacement == "remove":
            repl = ""
        else:  # token
            token = tokenize(original, pepper or "", namespace=f"{namespace}:{f.entity_type}", length=12)
            repl = f"[{f.entity_type}:{token}]"
        out = out[: f.start] + repl + out[f.end :]
    return out


def scan_texts(
    texts: Sequence[Any],
    entities: Sequence[str] | None = None,
    *,
    score_threshold: float = 0.35,
    backend: str = "auto",
) -> dict[str, int]:
    """Aggregate entity-type hit counts across an iterable of texts. PHI-free result."""
    hits: Counter[str] = Counter()
    for value in texts:
        if not value:
            continue
        for f in analyze_text(str(value), entities, score_threshold=score_threshold, backend=backend):
            hits[f.entity_type] += 1
    return dict(hits)


# --------------------------------------------------------------------------------------
# Spark wrappers (only imported/used inside Fabric)
# --------------------------------------------------------------------------------------
def _make_redact_series_fn(
    entities: Sequence[str] | None,
    replacement: str,
    pepper: str | None,
    namespace: str,
    score_threshold: float,
    backend: str,
):
    """Build a function mapping an iterable of texts -> list of redacted texts (per partition)."""

    def _fn(values: Sequence[Any]) -> list[Any]:
        return [
            redact_text(
                None if v is None else str(v),
                entities=entities,
                replacement=replacement,
                pepper=pepper,
                namespace=namespace,
                score_threshold=score_threshold,
                backend=backend,
            )
            for v in values
        ]

    return _fn


def redact_text_column(
    df,
    column: str,
    *,
    entities: Sequence[str] | None = None,
    replacement: str = "label",
    pepper: str | None = None,
    namespace: str = "free_text",
    score_threshold: float = 0.35,
    backend: str = "auto",
    output_column: str | None = None,
):
    """Return ``df`` with ``column`` redacted (in place, or into ``output_column``).

    Prefers a vectorized ``pandas_udf`` (one analyzer build per worker, batched rows) for
    throughput and falls back to a row-wise UDF when Arrow/pandas is unavailable. The analyzer
    singleton means Presidio's model loads once per executor, not once per row.
    """
    from pyspark.sql import functions as F  # type: ignore
    from pyspark.sql import types as T  # type: ignore

    target = output_column or column
    series_fn = _make_redact_series_fn(
        entities, replacement, pepper, namespace, score_threshold, backend
    )

    try:
        import pandas as pd  # type: ignore  # noqa: F401

        @F.pandas_udf(T.StringType())
        def _redact_udf(col: pd.Series) -> pd.Series:  # type: ignore
            import pandas as _pd  # type: ignore

            return _pd.Series(series_fn(list(col)))

        return df.withColumn(target, _redact_udf(F.col(column)))
    except Exception:
        # Arrow/pandas not available: fall back to a plain row UDF.
        def _one(v):  # noqa: ANN001
            return series_fn([v])[0]

        row_udf = F.udf(_one, T.StringType())
        return df.withColumn(target, row_udf(F.col(column)))


def scan_text_column(
    df,
    column: str,
    *,
    entities: Sequence[str] | None = None,
    score_threshold: float = 0.35,
    backend: str = "auto",
    sample_limit: int | None = 10000,
) -> dict[str, int]:
    """Scan a free-text column for identifier entities; return ``{entity_type: count}``.

    Distributed detection via an ``array<string>`` UDF of entity types, then a distributed
    ``explode`` + ``groupBy`` so only the small aggregate (one row per entity type) returns to
    the driver. ``sample_limit`` bounds the scan for a fast gate; set None to scan every row.
    """
    from pyspark.sql import functions as F  # type: ignore
    from pyspark.sql import types as T  # type: ignore

    def _types(v):  # noqa: ANN001
        if v is None:
            return []
        return [f.entity_type for f in analyze_text(str(v), entities, score_threshold=score_threshold, backend=backend)]

    types_udf = F.udf(_types, T.ArrayType(T.StringType()))
    scan_df = df.select(column)
    if sample_limit is not None:
        scan_df = scan_df.limit(sample_limit)

    exploded = scan_df.select(F.explode(types_udf(F.col(column))).alias("_entity_type"))
    agg = exploded.groupBy("_entity_type").agg(F.count(F.lit(1)).alias("n")).collect()
    return {r["_entity_type"]: int(r["n"]) for r in agg}
