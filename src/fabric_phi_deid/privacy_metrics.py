"""
privacy_metrics.py — quasi-identifier disclosure-risk metrics and k-anonymity enforcement.

Why this exists
---------------
Safe Harbor (strip the 18 identifiers) makes a dataset *presumptively* de-identified, but the
Expert Determination method reasons about **residual re-identification risk** arising from
combinations of quasi-identifiers (QIs) — e.g. ``{birth year, sex, 3-digit ZIP}``. These
metrics quantify that risk so a reviewer has hard evidence, and :func:`enforce_k_anonymity`
can mechanically reduce it.

Definitions (as applied here)
-----------------------------
- **Equivalence class** — records sharing an identical tuple of QI values.
- **k-anonymity** — every equivalence class has >= k records, so no record is distinguishable
  from at least ``k-1`` others on the QIs. Reported ``k`` is the *minimum* class size.
- **l-diversity (distinct)** — every class contains >= l distinct values of the sensitive
  attribute. Guards against the "k-anonymous but every patient in the class has the same
  diagnosis" attribute-disclosure that k-anonymity alone misses.
- **t-closeness** — the sensitive-attribute distribution within each class is within distance
  ``t`` (total-variation distance for categoricals) of the global distribution. Reported ``t``
  is the *maximum* over classes; smaller is closer, hence safer.

Safety
------
Everything here is **pure Python and aggregate-only** by default: it reads QI + sensitive
values to compute counts, but the reports carry only statistics (sizes, histograms, counts) —
never row-level data. Concrete QI value combinations are included in a report only when the
caller opts in with ``include_examples=True``. Optional Spark wrappers push the same logic to
table scale without collecting rows to the driver.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "equivalence_classes",
    "KAnonymityReport",
    "LDiversityReport",
    "TClosenessReport",
    "SuppressionReport",
    "measure_k_anonymity",
    "measure_l_diversity",
    "measure_t_closeness",
    "enforce_k_anonymity",
    "measure_k_anonymity_spark",
    "enforce_k_anonymity_spark",
]


Record = Mapping[str, Any]


# --------------------------------------------------------------------------------------
# Pure-Python core
# --------------------------------------------------------------------------------------
def _qi_key(record: Record, quasi_identifiers: Sequence[str]) -> tuple:
    """Return the QI-value tuple that identifies a record's equivalence class."""
    return tuple(record.get(q) for q in quasi_identifiers)


def equivalence_classes(
    records: Sequence[Record], quasi_identifiers: Sequence[str]
) -> dict[tuple, list[int]]:
    """Group record *indices* by their quasi-identifier tuple. Single O(n) pass."""
    classes: dict[tuple, list[int]] = {}
    for i, rec in enumerate(records):
        classes.setdefault(_qi_key(rec, quasi_identifiers), []).append(i)
    return classes


def _size_histogram(sizes: Sequence[int]) -> dict[int, int]:
    """size -> number of classes of that size (aggregate, always PHI-safe to persist)."""
    return dict(sorted(Counter(sizes).items()))


@dataclass
class KAnonymityReport:
    """k-anonymity measurement over a set of quasi-identifiers. Aggregate-only by default."""

    quasi_identifiers: list[str]
    k: int
    threshold: int | None
    num_records: int
    num_classes: int
    violating_classes: int
    violating_records: int
    class_size_histogram: dict[int, int] = field(default_factory=dict)
    smallest_class_examples: list[tuple[tuple, int]] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        """True when no threshold was set, or the minimum class size meets it."""
        return self.threshold is None or self.k >= self.threshold

    def summary(self) -> str:
        target = "" if self.threshold is None else f" (target k>={self.threshold})"
        verdict = "PASS" if self.passes else "FAIL"
        return (
            f"[k-anonymity {verdict}] k={self.k}{target} over {self.quasi_identifiers}: "
            f"{self.num_records} rows in {self.num_classes} classes; "
            f"{self.violating_records} rows in {self.violating_classes} under-threshold classes"
        )


def measure_k_anonymity(
    records: Sequence[Record],
    quasi_identifiers: Sequence[str],
    k: int | None = None,
    *,
    top: int = 5,
    include_examples: bool = False,
) -> KAnonymityReport:
    """Measure k-anonymity of ``records`` over ``quasi_identifiers``.

    ``k`` (optional) is the threshold to test against; ``top`` bounds how many smallest-class
    examples are returned when ``include_examples`` is set.
    """
    quasi_identifiers = list(quasi_identifiers)
    classes = equivalence_classes(records, quasi_identifiers)
    sizes = [len(idx) for idx in classes.values()]
    min_k = min(sizes) if sizes else 0

    violating_classes = 0
    violating_records = 0
    if k is not None:
        for size in sizes:
            if size < k:
                violating_classes += 1
                violating_records += size

    examples: list[tuple[tuple, int]] = []
    if include_examples:
        examples = sorted(
            ((key, len(idx)) for key, idx in classes.items()), key=lambda kv: kv[1]
        )[:top]

    return KAnonymityReport(
        quasi_identifiers=quasi_identifiers,
        k=min_k,
        threshold=k,
        num_records=len(records),
        num_classes=len(classes),
        violating_classes=violating_classes,
        violating_records=violating_records,
        class_size_histogram=_size_histogram(sizes),
        smallest_class_examples=examples,
    )


@dataclass
class LDiversityReport:
    """Distinct-l l-diversity measurement for a sensitive attribute within QI classes."""

    quasi_identifiers: list[str]
    sensitive_attribute: str
    l: int  # noqa: E741 - matches the published metric name
    threshold: int | None
    num_classes: int
    violating_classes: int
    violating_records: int
    worst_class_examples: list[tuple[tuple, int]] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return self.threshold is None or self.l >= self.threshold

    def summary(self) -> str:
        target = "" if self.threshold is None else f" (target l>={self.threshold})"
        verdict = "PASS" if self.passes else "FAIL"
        return (
            f"[l-diversity {verdict}] l={self.l}{target} for '{self.sensitive_attribute}' "
            f"over {self.quasi_identifiers}: {self.violating_records} rows in "
            f"{self.violating_classes} under-diverse classes"
        )


def measure_l_diversity(
    records: Sequence[Record],
    quasi_identifiers: Sequence[str],
    sensitive_attribute: str,
    l: int | None = None,  # noqa: E741 - "l" matches the published metric name
    *,
    top: int = 5,
    include_examples: bool = False,
) -> LDiversityReport:
    """Measure distinct l-diversity of ``sensitive_attribute`` within QI equivalence classes.

    ``l`` is the number of *distinct* sensitive values required in every class. Reported ``l``
    is the minimum distinct-value count across classes.
    """
    quasi_identifiers = list(quasi_identifiers)
    classes = equivalence_classes(records, quasi_identifiers)

    distinct_per_class: dict[tuple, int] = {}
    size_per_class: dict[tuple, int] = {}
    for key, idx in classes.items():
        distinct_per_class[key] = len({records[i].get(sensitive_attribute) for i in idx})
        size_per_class[key] = len(idx)

    min_l = min(distinct_per_class.values()) if distinct_per_class else 0

    violating_classes = 0
    violating_records = 0
    if l is not None:
        for key, distinct in distinct_per_class.items():
            if distinct < l:
                violating_classes += 1
                violating_records += size_per_class[key]

    examples: list[tuple[tuple, int]] = []
    if include_examples:
        examples = sorted(distinct_per_class.items(), key=lambda kv: kv[1])[:top]

    return LDiversityReport(
        quasi_identifiers=quasi_identifiers,
        sensitive_attribute=sensitive_attribute,
        l=min_l,
        threshold=l,
        num_classes=len(classes),
        violating_classes=violating_classes,
        violating_records=violating_records,
        worst_class_examples=examples,
    )


def _total_variation_distance(local: Counter, total_local: int, global_dist: Mapping[Any, float]) -> float:
    """0.5 * sum_v |p_local(v) - p_global(v)| over the union of support. In [0, 1]."""
    if total_local == 0:
        return 0.0
    keys = set(local) | set(global_dist)
    acc = 0.0
    for v in keys:
        p_local = local.get(v, 0) / total_local
        p_global = global_dist.get(v, 0.0)
        acc += abs(p_local - p_global)
    return 0.5 * acc


@dataclass
class TClosenessReport:
    """t-closeness measurement (categorical total-variation distance) per QI class."""

    quasi_identifiers: list[str]
    sensitive_attribute: str
    t: float
    threshold: float | None
    num_classes: int
    violating_classes: int
    violating_records: int
    worst_class_examples: list[tuple[tuple, float]] = field(default_factory=list)

    @property
    def passes(self) -> bool:
        return self.threshold is None or self.t <= self.threshold

    def summary(self) -> str:
        target = "" if self.threshold is None else f" (target t<={self.threshold})"
        verdict = "PASS" if self.passes else "FAIL"
        return (
            f"[t-closeness {verdict}] t={self.t:.3f}{target} for '{self.sensitive_attribute}' "
            f"over {self.quasi_identifiers}: {self.violating_records} rows in "
            f"{self.violating_classes} over-threshold classes"
        )


def measure_t_closeness(
    records: Sequence[Record],
    quasi_identifiers: Sequence[str],
    sensitive_attribute: str,
    t: float | None = None,
    *,
    top: int = 5,
    include_examples: bool = False,
) -> TClosenessReport:
    """Measure categorical t-closeness of ``sensitive_attribute`` across QI classes.

    Uses total-variation distance between each class's sensitive-value distribution and the
    global distribution. Reported ``t`` is the maximum distance over classes.
    """
    quasi_identifiers = list(quasi_identifiers)
    classes = equivalence_classes(records, quasi_identifiers)

    global_counter: Counter = Counter(records[i].get(sensitive_attribute) for i in range(len(records)))
    total = sum(global_counter.values())
    global_dist: dict[Any, float] = (
        {v: c / total for v, c in global_counter.items()} if total else {}
    )

    distances: dict[tuple, float] = {}
    sizes: dict[tuple, int] = {}
    for key, idx in classes.items():
        local = Counter(records[i].get(sensitive_attribute) for i in idx)
        distances[key] = _total_variation_distance(local, len(idx), global_dist)
        sizes[key] = len(idx)

    max_t = max(distances.values()) if distances else 0.0

    violating_classes = 0
    violating_records = 0
    if t is not None:
        for key, dist in distances.items():
            if dist > t:
                violating_classes += 1
                violating_records += sizes[key]

    examples: list[tuple[tuple, float]] = []
    if include_examples:
        examples = sorted(distances.items(), key=lambda kv: kv[1], reverse=True)[:top]

    return TClosenessReport(
        quasi_identifiers=quasi_identifiers,
        sensitive_attribute=sensitive_attribute,
        t=max_t,
        threshold=t,
        num_classes=len(classes),
        violating_classes=violating_classes,
        violating_records=violating_records,
        worst_class_examples=examples,
    )


@dataclass
class SuppressionReport:
    """Outcome of k-anonymity enforcement by record suppression."""

    quasi_identifiers: list[str]
    k: int
    num_records_in: int
    num_records_out: int
    suppressed_records: int
    suppressed_classes: int
    retained_classes: int

    @property
    def suppression_rate(self) -> float:
        return self.suppressed_records / self.num_records_in if self.num_records_in else 0.0

    def summary(self) -> str:
        return (
            f"[k-anon enforce k>={self.k}] kept {self.num_records_out}/{self.num_records_in} rows "
            f"(suppressed {self.suppressed_records}, {self.suppression_rate:.1%}) across "
            f"{self.retained_classes} retained / {self.suppressed_classes} dropped classes"
        )


def enforce_k_anonymity(
    records: Sequence[Record],
    quasi_identifiers: Sequence[str],
    k: int,
) -> tuple[list[Record], SuppressionReport]:
    """Enforce k-anonymity by **record suppression**.

    Every record whose equivalence class has fewer than ``k`` members is dropped; the rest are
    retained in their original order. Record suppression is the HHS-recognized, order-preserving
    way to guarantee a k floor without a generalization hierarchy — it trades some data volume
    for a hard k guarantee (measure the ``suppression_rate`` to judge the cost).

    Returns ``(kept_records, report)``. ``kept_records`` references the original record objects.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}.")
    quasi_identifiers = list(quasi_identifiers)
    classes = equivalence_classes(records, quasi_identifiers)

    kept_indices: set[int] = set()
    suppressed_classes = 0
    retained_classes = 0
    for idx in classes.values():
        if len(idx) >= k:
            retained_classes += 1
            kept_indices.update(idx)
        else:
            suppressed_classes += 1

    kept = [records[i] for i in range(len(records)) if i in kept_indices]
    report = SuppressionReport(
        quasi_identifiers=quasi_identifiers,
        k=k,
        num_records_in=len(records),
        num_records_out=len(kept),
        suppressed_records=len(records) - len(kept),
        suppressed_classes=suppressed_classes,
        retained_classes=retained_classes,
    )
    return kept, report


# --------------------------------------------------------------------------------------
# Spark wrappers (only imported/used inside Fabric)
# --------------------------------------------------------------------------------------
def measure_k_anonymity_spark(
    df,
    quasi_identifiers: Sequence[str],
    k: int | None = None,
) -> KAnonymityReport:
    """Distributed k-anonymity measurement. Aggregates on the cluster; no row collection.

    Groups by the quasi-identifiers, then reduces to the class-size distribution driver-side
    (bounded by the number of distinct class sizes, not by row count).
    """
    from pyspark.sql import functions as F  # type: ignore

    quasi_identifiers = list(quasi_identifiers)
    grouped = df.groupBy(*quasi_identifiers).count()

    # Class-size histogram: number of classes at each size. Small, safe to collect.
    hist_rows = grouped.groupBy("count").agg(F.count(F.lit(1)).alias("n_classes")).collect()
    histogram = {int(r["count"]): int(r["n_classes"]) for r in hist_rows}
    histogram = dict(sorted(histogram.items()))

    num_classes = sum(histogram.values())
    num_records = sum(size * n for size, n in histogram.items())
    min_k = min(histogram) if histogram else 0

    violating_classes = sum(n for size, n in histogram.items() if k is not None and size < k)
    violating_records = sum(size * n for size, n in histogram.items() if k is not None and size < k)

    return KAnonymityReport(
        quasi_identifiers=quasi_identifiers,
        k=min_k,
        threshold=k,
        num_records=num_records,
        num_classes=num_classes,
        violating_classes=violating_classes,
        violating_records=violating_records,
        class_size_histogram=histogram,
        smallest_class_examples=[],  # examples intentionally not pulled from Spark
    )


def enforce_k_anonymity_spark(df, quasi_identifiers: Sequence[str], k: int):
    """Return a new DataFrame containing only rows in equivalence classes of size >= ``k``.

    Uses a windowed count over the quasi-identifiers so the whole operation stays distributed
    (no groupBy/join round-trip, no driver collection). The helper column is dropped on the way
    out, so the output schema is unchanged.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}.")
    from pyspark.sql import Window  # type: ignore
    from pyspark.sql import functions as F  # type: ignore

    window = Window.partitionBy(*[F.col(c) for c in quasi_identifiers])
    counted = df.withColumn("_eqclass_size", F.count(F.lit(1)).over(window))
    return counted.filter(F.col("_eqclass_size") >= k).drop("_eqclass_size")
