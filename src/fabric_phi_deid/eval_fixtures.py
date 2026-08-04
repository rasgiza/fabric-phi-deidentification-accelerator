"""
eval_fixtures.py — small, fully-synthetic labeled corpus for free-text detector recall.

Shipped so the scorecard can publish an **actual** recall / precision / F1 number for the
free-text NER path (:mod:`fabric_phi_deid.ner_text`) instead of only asserting the capability
exists. Peer-reviewed clinical de-id tools report measured recall; this fixture lets the
accelerator do the same on data it can distribute.

Every example is invented — no real person, MRN, contact detail, or record appears here. Each
note is assembled from labeled parts by :func:`_note`, which computes the character offsets
automatically, so the :class:`~fabric_phi_deid.eval_harness.GoldSpan` offsets are correct by
construction (never hand-counted).

Entity types match what :mod:`ner_text` emits. Structured identifiers (``US_SSN``,
``PHONE_NUMBER``, ``EMAIL_ADDRESS``, ``CREDIT_CARD``, ``IP_ADDRESS``, ``URL``,
``MEDICAL_RECORD_NUMBER``) are catchable by the dependency-free regex fallback; contextual
identifiers (``PERSON``, ``LOCATION``, ``DATE_TIME``) require the Presidio backend
(``pip install 'fabric-phi-deid[nlp]'``). A run on the regex fallback will therefore miss the
contextual half — which is exactly the honest signal the scorecard should surface.
"""

from __future__ import annotations

from .eval_harness import GoldSpan

__all__ = ["FREE_TEXT_PHI_NOTES", "gold_span_count"]


def _note(*parts: object) -> tuple[str, tuple[GoldSpan, ...]]:
    """Assemble a note from labeled parts, computing gold-span offsets automatically."""
    text = ""
    spans: list[GoldSpan] = []
    for part in parts:
        if isinstance(part, tuple):
            entity_type, value = part
            start = len(text)
            text += value
            spans.append(GoldSpan(start, len(text), entity_type))
        else:
            text += str(part)
    return text, tuple(spans)


# Seven synthetic clinical-note snippets with labeled PHI spans. Deliberately balanced between
# regex-catchable (structured) and Presidio-only (contextual) identifiers so the measured recall
# honestly reflects which backend is active.
#
# Formatting is pinned below: one labeled part per line keeps the note text readable next to its
# entity label. Letting the formatter collapse these makes the corpus much harder to review.
# fmt: off
FREE_TEXT_PHI_NOTES: tuple[tuple[str, tuple[GoldSpan, ...]], ...] = (
    _note(
        "Patient ", ("PERSON", "John Alvarez"), " (DOB ", ("DATE_TIME", "03/14/1972"),
        ") reports chest pain. Call ", ("PHONE_NUMBER", "212-555-0182"), " or email ",
        ("EMAIL_ADDRESS", "j.alvarez@example.com"), ".",
    ),
    _note(
        "Ms. ", ("PERSON", "Maria Chen"), " lives in ", ("LOCATION", "Springfield"),
        ", IL. SSN ", ("US_SSN", "512-84-9021"), " on file. Follow-up ",
        ("DATE_TIME", "2025-06-01"), ".",
    ),
    _note(
        "Records faxed to provider portal ",
        ("URL", "https://portal.example.org/pt/8891"), " from ",
        ("IP_ADDRESS", "10.0.4.22"), ".",
    ),
    _note(
        "Payment card ", ("CREDIT_CARD", "4111 1111 1111 1111"),
        " declined; patient ", ("PERSON", "Robert Lee"), " to update billing.",
    ),
    _note(
        "Dr. ", ("PERSON", "Susan Park"), " reached at ",
        ("EMAIL_ADDRESS", "susan.park@clinic.example"), "; cell ",
        ("PHONE_NUMBER", "415-555-0147"), ".",
    ),
    _note(
        "Discharge ", ("DATE_TIME", "07/22/2025"), ". Contact next of kin at ",
        ("PHONE_NUMBER", "617-555-0199"), ". Seen in ", ("LOCATION", "Boston"), ".",
    ),
    # MRN is HIPAA identifier #7 and Presidio ships no recognizer for it, so ner_text always
    # applies its own regex. This note keeps that path measured rather than assumed.
    _note(
        "Triage note: ", ("PERSON", "Alexa Castillo"), ", ",
        ("MEDICAL_RECORD_NUMBER", "MRN00000002"), ", chest pain. Confirmed by phone ",
        ("PHONE_NUMBER", "212-555-0111"), ".",
    ),
)
# fmt: on


def gold_span_count() -> int:
    """Total number of labeled gold spans across the fixture."""
    return sum(len(spans) for _text, spans in FREE_TEXT_PHI_NOTES)
