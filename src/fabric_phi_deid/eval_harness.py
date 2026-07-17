"""
eval_harness.py — precision / recall / F1 for the PHI detectors.

A de-identification tool is only as trustworthy as its detectors, and "it looks clean" is not
evidence. This module scores a detector against **labeled ground truth** so the accelerator can
publish the same kind of quantitative quality claim that peer-reviewed clinical de-id tools do
(precision, recall, F1) instead of an unverifiable assertion.

Two granularities
------------------
- **Set / value level** (:func:`evaluate_sets`, :func:`evaluate_flags`) — did we flag the right
  items? Use for the structured residual-PHI scanner (a value either is or isn't residual PHI).
- **Span level** (:func:`evaluate_spans`) — for the free-text NER path, did we find the right
  character spans? A predicted span counts as a true positive when it overlaps a gold span by at
  least ``min_overlap`` (Jaccard over character offsets), optionally requiring the entity type to
  match too.

Pure Python, no data dependencies — labels are supplied by the caller (typically a small,
hand-labeled synthetic fixture) so the harness itself never touches real PHI.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ClassificationMetrics",
    "GoldSpan",
    "evaluate_sets",
    "evaluate_flags",
    "evaluate_spans",
]


@dataclass(frozen=True)
class ClassificationMetrics:
    """Standard binary-detection metrics derived from confusion counts."""

    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def support(self) -> int:
        """Number of gold positives (tp + fn)."""
        return self.true_positives + self.false_negatives

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict[str, float | int]:
        return {
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "support": self.support,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }

    def summary(self) -> str:
        return (
            f"precision={self.precision:.3f} recall={self.recall:.3f} f1={self.f1:.3f} "
            f"(tp={self.true_positives} fp={self.false_positives} fn={self.false_negatives})"
        )


def evaluate_sets(predicted: Iterable[Any], gold: Iterable[Any]) -> ClassificationMetrics:
    """Score predicted vs. gold as sets of hashable items (e.g. flagged row ids or values)."""
    p = set(predicted)
    g = set(gold)
    tp = len(p & g)
    return ClassificationMetrics(
        true_positives=tp,
        false_positives=len(p - g),
        false_negatives=len(g - p),
    )


def evaluate_flags(pairs: Iterable[tuple[bool, bool]]) -> ClassificationMetrics:
    """Score an iterable of ``(predicted_positive, gold_positive)`` boolean pairs."""
    tp = fp = fn = 0
    for predicted, gold in pairs:
        if predicted and gold:
            tp += 1
        elif predicted and not gold:
            fp += 1
        elif not predicted and gold:
            fn += 1
    return ClassificationMetrics(true_positives=tp, false_positives=fp, false_negatives=fn)


@dataclass(frozen=True)
class GoldSpan:
    """A labeled character span in a text: ``[start, end)`` with an entity type."""

    start: int
    end: int
    entity_type: str


def _jaccard(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Character-offset Jaccard overlap of two half-open spans. 0 when disjoint."""
    inter = max(0, min(a_end, b_end) - max(a_start, b_start))
    if inter == 0:
        return 0.0
    union = (a_end - a_start) + (b_end - b_start) - inter
    return inter / union if union else 0.0


def evaluate_spans(
    predicted: Sequence[Any],
    gold: Sequence[GoldSpan],
    *,
    min_overlap: float = 0.5,
    match_type: bool = True,
) -> ClassificationMetrics:
    """Score predicted spans against gold spans by greedy best-overlap matching.

    ``predicted`` items must expose ``.start``, ``.end`` and ``.entity_type`` (e.g.
    :class:`fabric_phi_deid.ner_text.TextFinding`). A prediction matches a gold span when their
    Jaccard overlap is >= ``min_overlap`` and — if ``match_type`` — the entity types are equal.
    Each gold span is consumed by at most one prediction; unmatched predictions are false
    positives and unmatched gold spans are false negatives.
    """
    remaining = list(range(len(gold)))
    tp = 0
    fp = 0
    for pred in predicted:
        best_j = -1.0
        best_i = -1
        for pos, gi in enumerate(remaining):
            g = gold[gi]
            if match_type and getattr(pred, "entity_type", None) != g.entity_type:
                continue
            j = _jaccard(pred.start, pred.end, g.start, g.end)
            if j >= min_overlap and j > best_j:
                best_j = j
                best_i = pos
        if best_i >= 0:
            tp += 1
            remaining.pop(best_i)
        else:
            fp += 1
    return ClassificationMetrics(
        true_positives=tp, false_positives=fp, false_negatives=len(remaining)
    )
