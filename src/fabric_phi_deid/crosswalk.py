"""
crosswalk.py — randomly *assigned* re-identification codes (HIPAA §164.514(c)).

Why this module exists
----------------------
``tokenize`` produces a key by HMAC-ing the identifier. That is excellent for Expert
Determination — it is deterministic, so two source systems holding the same MRN conform
without either ever sharing the MRN — but it is **derived from the individual**, which
disqualifies a Safe Harbor claim (§164.514(b)(2)(R), and see HHS guidance §3.2: even
patient initials are too much).

§164.514(c) permits the other kind of key:

    "A covered entity may assign a code or other means of record identification to allow
    information de-identified under this section to be re-identified by the covered
    entity, provided that: (1) *Derivation.* The code ... is not derived from or related
    to information about the individual and is not otherwise capable of being translated
    so as to identify the individual; and (2) *Security.* The covered entity does not use
    or disclose the code ... for any other purpose and does not disclose the mechanism for
    re-identification."

So linkage under Safe Harbor is not forbidden — *deriving* the link from the patient is.
A code drawn from a CSPRNG carries no information about anybody. It is only a re-
identification risk to a holder of the mapping, which is why the mapping is minted once,
in the Vault, and never travels with the data.

This is the difference in one line::

    tokenize    : id = HMAC(pepper, MRN)      # reproducible from the MRN  -> derived
    surrogate   : id = secrets.token_hex(8)   # reproducible from nothing  -> assigned

Consequences that are easy to get wrong
---------------------------------------
* **The mapping must be persisted, not recomputed.** A token can be regenerated from the
  source value forever; an assigned code cannot. Lose ``xwalk_patient_surrogate`` and the
  link is gone permanently — which is a *feature* for disclosure risk and a *hazard* for
  operations. Back it up inside the Vault, never outside it.
* **Never mint inside a UDF.** Minting is non-deterministic, so a Spark task retry or a
  re-partition would hand the same patient two different codes. Mint once, on the driver,
  from the distinct identifier list; then broadcast the finished mapping and *look up*.
  :func:`~fabric_phi_deid.deid_engine.strat_surrogate` therefore fails closed on a miss
  rather than inventing a code.
* **One mapping spans all source systems.** The whole point is that Caboodle and Clarity
  land on the same code for the same human, so the crosswalk is minted from the *union*
  of identifiers before either silver table is de-identified.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Mapping

__all__ = [
    "SURROGATE_PREFIX",
    "SURROGATE_ID_BYTES",
    "SURROGATE_COLUMN",
    "CROSSWALK_TABLE",
    "CROSSWALK_SOURCE_COLUMN",
    "mint_surrogate_id",
    "mint_crosswalk",
    "crosswalk_to_rows",
    "crosswalk_from_rows",
]

#: Prefix on every assigned code. Purely cosmetic — it makes a stray value obvious in a
#: query result and distinguishable from a ``PT-`` HMAC token at a glance.
SURROGATE_PREFIX = "DEID-"

#: Bytes of CSPRNG entropy per code. 8 bytes = 16 hex chars = 2**64 space; at 10M patients
#: the birthday collision probability is ~2.7e-6, and :func:`mint_crosswalk` re-draws on
#: collision anyway, so this is about keeping the code short, not about safety.
SURROGATE_ID_BYTES = 8

#: Column the assigned code lands in, in both silver and gold.
SURROGATE_COLUMN = "DeidPatientID"

#: Vault-only table holding the mapping. Named ``xwalk_*`` like the token crosswalk so the
#: existing Vault RLS/RBAC and the ``xwalk_*`` exclusion rules cover it without amendment.
CROSSWALK_TABLE = "xwalk_patient_surrogate"

#: Column in :data:`CROSSWALK_TABLE` holding the identified value the code stands in for.
CROSSWALK_SOURCE_COLUMN = "SourceIdentifier"


def mint_surrogate_id(*, prefix: str = SURROGATE_PREFIX, nbytes: int = SURROGATE_ID_BYTES) -> str:
    """Draw one assigned code from the OS CSPRNG.

    Takes no value to key on — deliberately. If this function accepted the identifier it
    would be one careless edit away from becoming a hash, and the §164.514(c)(1) argument
    would quietly stop being true.
    """
    if nbytes < 1:
        raise ValueError(f"nbytes must be >= 1, got {nbytes}")
    return f"{prefix}{secrets.token_hex(nbytes)}"


def mint_crosswalk(
    identifiers: Iterable[str],
    *,
    existing: Mapping[str, str] | None = None,
    prefix: str = SURROGATE_PREFIX,
    nbytes: int = SURROGATE_ID_BYTES,
) -> dict[str, str]:
    """Return ``{identifier: assigned_code}`` covering every identifier given.

    Parameters
    ----------
    identifiers:
        The identified values to assign codes to — typically the union of Caboodle MRNs
        and Clarity ``PAT_MRN_ID`` values, so both systems conform on one code. Duplicates
        and blanks are ignored.
    existing:
        A previously minted mapping, read back from :data:`CROSSWALK_TABLE`. Identifiers
        already present keep their code, so re-running the pipeline does not renumber the
        population and break every published report. Only genuinely new patients are
        minted. **Pass this on every run after the first.**

    Notes
    -----
    Codes already in ``existing`` are excluded from the draw, so a re-run cannot issue a
    code that is live under a different patient.
    """
    mapping = dict(existing or {})
    taken = set(mapping.values())

    for raw in identifiers:
        if raw is None:
            continue
        key = str(raw).strip()
        if not key or key in mapping:
            continue
        while True:
            code = mint_surrogate_id(prefix=prefix, nbytes=nbytes)
            if code not in taken:
                break
        mapping[key] = code
        taken.add(code)

    return mapping


def crosswalk_to_rows(
    mapping: Mapping[str, str],
    *,
    source_column: str = CROSSWALK_SOURCE_COLUMN,
    surrogate_column: str = SURROGATE_COLUMN,
) -> list[dict[str, str]]:
    """Flatten a mapping into rows for writing to the Vault Delta table."""
    return [{source_column: source, surrogate_column: code} for source, code in mapping.items()]


def crosswalk_from_rows(
    rows: Iterable[Mapping[str, str]],
    *,
    source_column: str = CROSSWALK_SOURCE_COLUMN,
    surrogate_column: str = SURROGATE_COLUMN,
) -> dict[str, str]:
    """Rebuild a mapping from Vault rows, refusing anything ambiguous.

    Raises on a duplicated source identifier carrying two different codes, or on one code
    reused across two patients. Either would mean the crosswalk had been written twice
    without ``existing=`` and is no longer a function in both directions — silently
    tolerating it would conform two different people onto one key.
    """
    mapping: dict[str, str] = {}
    owner: dict[str, str] = {}
    for row in rows:
        source = str(row[source_column])
        code = str(row[surrogate_column])
        if mapping.setdefault(source, code) != code:
            raise ValueError(
                f"Crosswalk is corrupt: {source_column}={source!r} maps to both "
                f"{mapping[source]!r} and {code!r}. Restore {CROSSWALK_TABLE} from a "
                "Vault backup rather than de-identifying against it."
            )
        if owner.setdefault(code, source) != source:
            raise ValueError(
                f"Crosswalk is corrupt: {surrogate_column}={code!r} is assigned to both "
                f"{owner[code]!r} and {source!r}. Two people would conform onto one key."
            )
    return mapping
