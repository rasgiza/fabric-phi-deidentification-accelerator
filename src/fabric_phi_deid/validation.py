"""
validation.py — PHI leak detection for de-identified output.

This is the programmatic core behind the ``NB_scorecard`` notebook: a set of regexes that
flag values that look like residual direct identifiers (SSN, phone, email, MRN-shaped
tokens that were NOT re-tokenized). It runs on OUTPUT that is supposed to be de-identified
— a last-line assertion gate before ``gold_safe_*`` is published.

The scanners are pure Python (regex over strings) so they are unit-testable and reusable
both in tests and, lazily wrapped, in a Spark job (see ``scan_spark_dataframe``).

Important: these patterns are heuristics. Passing the scorecard is necessary but NOT
sufficient for a HIPAA determination — see docs/pre_real_phi_checklist.md.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "PHI_PATTERNS",
    "scan_value_for_phi",
    "scan_values_for_phi",
    "scan_spark_dataframe",
]

# Named heuristics for residual direct identifiers in supposedly de-identified text.
PHI_PATTERNS: dict[str, re.Pattern[str]] = {
    # US SSN: 123-45-6789 (with or without separators). Excludes obviously-invalid 000 area.
    "ssn": re.compile(r"\b(?!000|666|9\d\d)\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"),
    # US phone: (212) 555-0123, 212-555-0123, +1 212 555 0123
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
}


def scan_value_for_phi(value: Any) -> list[str]:
    """Return the names of PHI patterns that MATCH ``value`` (empty list == clean).

    Non-string / None values are treated as clean (they carry no free-text identifier).
    """
    if value is None:
        return []
    if not isinstance(value, str):
        value = str(value)
    return [name for name, pattern in PHI_PATTERNS.items() if pattern.search(value)]


def scan_values_for_phi(values: list[Any]) -> dict[str, int]:
    """Aggregate pattern-hit counts across an iterable of values."""
    hits: dict[str, int] = {name: 0 for name in PHI_PATTERNS}
    for value in values:
        for name in scan_value_for_phi(value):
            hits[name] += 1
    return {name: count for name, count in hits.items() if count}


def scan_spark_dataframe(df, sample_limit: int | None = 10000) -> dict[str, dict[str, int]]:
    """Scan a Spark DataFrame's string columns for residual PHI patterns.

    Lazily imports PySpark. Returns ``{column: {pattern: hit_count}}`` for columns with any
    hits. ``sample_limit`` bounds the driver-side pull (set None to scan all rows — only do
    this on modest tables). Intended as an assertion gate, not a data export.
    """
    from pyspark.sql import types as T  # type: ignore

    string_cols = [f.name for f in df.schema.fields if isinstance(f.dataType, T.StringType)]
    if not string_cols:
        return {}

    scan_df = df.select(*string_cols)
    if sample_limit is not None:
        scan_df = scan_df.limit(sample_limit)

    results: dict[str, dict[str, int]] = {}
    for row in scan_df.collect():
        for col in string_cols:
            hits = scan_value_for_phi(row[col])
            if hits:
                bucket = results.setdefault(col, {})
                for name in hits:
                    bucket[name] = bucket.get(name, 0) + 1
    return results
