"""
deid_engine.py — config-driven PHI de-identification strategy dispatcher.

Two layers:

1. **Pure-Python core** (`apply_strategy` + the ``strat_*`` functions). No PySpark
   dependency, so every strategy is unit-testable locally. This is where the actual
   de-identification logic lives.
2. **Spark wrappers** (`build_column_expr`, `deidentify_table`). These lazily import
   PySpark and are only used inside a Microsoft Fabric notebook. They map the pure
   functions onto DataFrame columns as UDFs / column expressions.

Strategies (mirrors the Tonic Textual "generator" model, extended for structured Safe
Harbor de-identification):

- ``tokenize``     : deterministic keyed HMAC token (reversible only via the vault crosswalk)
- ``surrogate``    : randomly *assigned* code looked up in a pre-minted crosswalk. Same job as
  ``tokenize`` -- a stable per-patient key that conforms two source systems -- but drawn from a
  CSPRNG instead of derived from the identifier, which is what §164.514(c) requires and what
  lets a Safe Harbor profile link across systems at all. See :mod:`fabric_phi_deid.crosswalk`.
- ``synthesize``   : consistent fake-but-realistic value (irreversible, shareable)
- ``generalize``   : reduce precision — date->year, birth date->floored year, zip->3-digit,
  age cap 90+
- ``date_shift``   : shift a date by a per-entity consistent offset (preserves intervals)
- ``suppress``     : drop the value (fail-safe default for unclassified columns)
- ``passthrough``  : leave the value unchanged (non-PHI columns explicitly allow-listed)

The config (`config/deid_rules.yaml`) selects a **profile** (``safe_harbor`` or
``expert_determination``) and, per table, maps each column to a strategy + params. Any
column NOT listed falls to the profile ``default_strategy`` (which is ``suppress`` — a
deny-by-default posture so a newly-added, unclassified column can never silently leak).
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Callable
from typing import Any

from .tokenization import tokenize, tokenize_format_preserving

__all__ = [
    "apply_strategy",
    "load_rules",
    "resolve_column_strategy",
    "resolve_table_plan",
    "build_column_expr",
    "deidentify_table",
    "STRATEGIES",
    "RESTRICTED_ZIP3",
]

# --- ZIP prefixes that must be zeroed under HIPAA Safe Harbor (low-population 3-digit
# --- geographic areas). This is the HHS-published list; kept here for the demo.
# Public so verification (NB_scorecard) can assert against the same list the engine applies,
# rather than re-deriving it and risking a check that is weaker than the transform it checks.
RESTRICTED_ZIP3 = frozenset(
    {
        "036",
        "059",
        "063",
        "102",
        "203",
        "556",
        "692",
        "790",
        "821",
        "823",
        "830",
        "831",
        "878",
        "879",
        "884",
        "890",
        "893",
    }
)
_RESTRICTED_ZIP3 = RESTRICTED_ZIP3  # backwards-compatible private alias


# --------------------------------------------------------------------------------------
# Pure-Python strategy implementations
# --------------------------------------------------------------------------------------
def strat_passthrough(value: Any, params: dict, pepper: str) -> Any:
    return value


def strat_suppress(value: Any, params: dict, pepper: str) -> Any:
    """Drop the value. Returns None by default, or a fixed redaction token if configured."""
    return params.get("redaction")  # None unless a placeholder like "***" is set


def strat_tokenize(value: Any, params: dict, pepper: str) -> Any:
    """Deterministic keyed token.

    ``strip_separators`` may be set per-column in the rulebook when two source systems
    write the same identifier with different punctuation (``A12-3456`` vs ``A123456``).
    It is off unless asked for, because removing separators can merge two genuinely
    different identifiers -- see tokenization.normalize_identifier.
    """
    if value is None or str(value).strip() == "":
        return value
    namespace = params.get("namespace", "default")
    strip_separators = bool(params.get("strip_separators", False))
    if params.get("format_preserving"):
        return tokenize_format_preserving(
            str(value), pepper, namespace=namespace, strip_separators=strip_separators
        )
    return tokenize(
        str(value),
        pepper,
        namespace=namespace,
        length=int(params.get("length", 16)),
        prefix=params.get("prefix", ""),
        strip_separators=strip_separators,
    )


def strat_surrogate(value: Any, params: dict, pepper: str) -> Any:
    """Replace the identifier with its randomly assigned code from a pre-minted crosswalk.

    ``params["crosswalk"]`` is a ``{identifier: code}`` mapping supplied by the *caller* --
    minted once on the driver by :func:`fabric_phi_deid.crosswalk.mint_crosswalk` and
    broadcast -- not something the rulebook can express. The rulebook says *that* a column
    is surrogate-keyed; the runtime says *which* codes.

    Fails closed on an unmapped identifier. Minting here instead would be tempting and
    wrong: this runs per-row inside a Spark task, so a retry or a re-partition would draw a
    second code for the same patient and split them into two people. An identifier reaching
    this function unmapped means the crosswalk was minted from an incomplete population, and
    that is a pipeline bug to fix upstream, not to paper over per row.
    """
    if value is None or str(value).strip() == "":
        return value
    crosswalk = params.get("crosswalk")
    if crosswalk is None:
        raise ValueError(
            "surrogate requires 'crosswalk' ({identifier: assigned_code}). Mint it with "
            "crosswalk.mint_crosswalk() over the union of source identifiers and pass it "
            "in -- it is runtime state, not a rulebook parameter."
        )
    key = str(value).strip()
    try:
        return crosswalk[key]
    except KeyError:
        raise KeyError(
            f"No assigned code for identifier {key!r}. The crosswalk was minted from a "
            "population that does not include this row -- re-mint over the union of ALL "
            "source systems before de-identifying any of them."
        ) from None


def strat_generalize(value: Any, params: dict, pepper: str) -> Any:
    """Reduce precision. ``kind`` selects the generalization:

    - ``year``      : a date -> its 4-digit year (int)
    - ``birth_year``: a BIRTH date -> its year, floored so it cannot imply an age over 89
    - ``zip3``      : a ZIP -> first 3 digits, or "000" if a restricted low-pop prefix
    - ``age_cap``   : an integer age -> capped at 90 (Safe Harbor: 90+ ages aggregated)
    """
    if value is None or value == "":
        return value
    kind = params.get("kind", "year")

    if kind == "year":
        d = _coerce_date(value)
        return d.year if d is not None else None

    if kind == "birth_year":
        # Safe Harbor §164.514(b)(2)(i)(C) removes ages over 89 *and* "all elements of dates
        # (including year) indicative of such age". Capping Age at 90 while publishing a true
        # birth year does not satisfy that: the year reconstructs the age the cap removed.
        # So every birth year old enough to imply 90+ collapses into one bucket, matching HHS's
        # worked example (born 1910, seen 2010 -> report "on or before 1920").
        d = _coerce_date(value)
        if d is None:
            return None
        cap = int(params.get("cap_age", 90))
        # reference_year pins the bucket so output is reproducible; left unset it tracks the
        # current year, which is correct for a live run but makes stored output drift over time.
        reference_year = params.get("reference_year")
        ref = int(reference_year) if reference_year else _dt.datetime.now(_dt.UTC).year
        return max(d.year, ref - cap)

    if kind == "zip3":
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if len(digits) < 3:
            return "000"
        prefix = digits[:3]
        return "000" if prefix in _RESTRICTED_ZIP3 else prefix

    if kind == "age_cap":
        cap = int(params.get("cap", 90))
        try:
            age = int(value)
        except (ValueError, TypeError):
            return None
        return cap if age >= cap else age

    raise ValueError(f"Unknown generalize kind: {kind!r}")


def strat_date_shift(value: Any, params: dict, pepper: str) -> Any:
    """Shift a date by a per-entity consistent offset in days.

    The offset is deterministically derived from an entity key (e.g. the patient token)
    so that ALL dates for the same entity move by the same amount — preserving intervals
    (length of stay, time-between-visits) while breaking the true calendar date. Requires
    ``entity_value`` to be supplied by the caller (the row's join key).
    """
    if value is None or value == "":
        return value
    d = _coerce_date(value)
    if d is None:
        return None
    entity_value = params.get("entity_value")
    if entity_value is None:
        raise ValueError("date_shift requires 'entity_value' (the row's entity key).")
    max_days = int(params.get("max_days", 365))
    # Derive a stable signed offset in [-max_days, +max_days] from the entity token.
    tok = tokenize(str(entity_value), pepper, namespace="date_shift", length=8)
    if tok is None:  # unreachable for a non-empty entity key; keeps typing sound
        return None
    offset = (int(tok, 16) % (2 * max_days + 1)) - max_days
    return d + _dt.timedelta(days=offset)


def strat_synthesize(value: Any, params: dict, pepper: str) -> Any:
    """Return a consistent, irreversible synthetic value (no vault needed).

    Deterministic in ``value`` so the same source always yields the same synthetic
    output (consistency), but the mapping is not stored anywhere, so it is not
    reversible. ``kind`` picks a small built-in generator (first_name/last_name/name);
    for production realism swap in Faker seeded by the token.
    """
    if value is None or value == "":
        return value
    kind = params.get("kind", "name")
    token = tokenize(str(value), pepper, namespace=f"synth_{kind}", length=8)
    if token is None:  # unreachable for a non-empty value; keeps typing sound
        return value
    idx = int(token, 16)
    if kind == "first_name":
        return _FIRST_NAMES[idx % len(_FIRST_NAMES)]
    if kind == "last_name":
        return _LAST_NAMES[idx % len(_LAST_NAMES)]
    if kind == "name":
        first = _FIRST_NAMES[idx % len(_FIRST_NAMES)]
        last = _LAST_NAMES[(idx // len(_FIRST_NAMES)) % len(_LAST_NAMES)]
        return f"{last}, {first}"
    raise ValueError(f"Unknown synthesize kind: {kind!r}")


def strat_redact_text(value: Any, params: dict, pepper: str) -> Any:
    """Redact identifiers found *inside* a free-text value (notes, comments, reason-for-visit).

    Delegates detection/redaction to :mod:`fabric_phi_deid.ner_text` (Presidio when installed,
    a regex fallback otherwise). ``params`` are forwarded: ``replacement`` (label/token/remove),
    ``entities`` (list), ``namespace``, ``score_threshold``, ``backend``. With ``token`` the
    matched substrings are HMAC-tokenized under the pepper so linkage survives redaction.
    """
    if value is None or value == "":
        return value
    from .ner_text import redact_text

    return redact_text(
        str(value),
        entities=params.get("entities"),
        replacement=params.get("replacement", "label"),
        pepper=pepper,
        namespace=params.get("namespace", "free_text"),
        score_threshold=float(params.get("score_threshold", 0.35)),
        backend=params.get("backend", "auto"),
    )


STRATEGIES: dict[str, Callable[[Any, dict, str], Any]] = {
    "passthrough": strat_passthrough,
    "suppress": strat_suppress,
    "tokenize": strat_tokenize,
    "surrogate": strat_surrogate,
    "generalize": strat_generalize,
    "date_shift": strat_date_shift,
    "synthesize": strat_synthesize,
    "redact_text": strat_redact_text,
}


def apply_strategy(value: Any, strategy: str, params: dict | None, pepper: str) -> Any:
    """Dispatch a single value through the named strategy. Pure function."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}. Valid: {sorted(STRATEGIES)}")
    return STRATEGIES[strategy](value, params or {}, pepper)


# --------------------------------------------------------------------------------------
# Config resolution
# --------------------------------------------------------------------------------------
def load_rules(path: str) -> dict:
    """Load, validate, and return the deid_rules.yaml config.

    Fails **fast**: a structurally/semantically invalid config raises
    :class:`~fabric_phi_deid.config.ConfigValidationError` *before* any data is touched,
    so a bad rulebook can never partially process PHI.
    """
    import yaml  # local import keeps module importable without PyYAML in odd envs

    from .config import ConfigValidationError, validate_config

    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    errors = validate_config(cfg)
    if errors:
        raise ConfigValidationError(errors)
    return cfg


def resolve_column_strategy(cfg: dict, profile: str, table: str, column: str) -> tuple[str, dict]:
    """Return (strategy, params) for a table.column under a profile.

    Falls back to the profile's ``default_strategy`` (deny-by-default = suppress) for any
    column not explicitly classified. This is the fail-safe that stops new/unknown
    columns from leaking.
    """
    if profile not in cfg["profiles"]:
        raise ValueError(f"Unknown profile {profile!r}.")
    prof = cfg["profiles"][profile]
    default_strategy = prof.get("default_strategy", "suppress")
    tables = prof.get("tables", {}) or {}
    table_rules = tables.get(table, {}) or {}
    col_rule = table_rules.get(column)
    if col_rule is None:
        return default_strategy, {}
    if isinstance(col_rule, str):
        return col_rule, {}
    # dict form: {strategy: ..., <params>}
    params = {k: v for k, v in col_rule.items() if k != "strategy"}
    return col_rule.get("strategy", default_strategy), params


def resolve_table_plan(
    cfg: dict, profile: str, table: str, columns: list[str]
) -> dict[str, tuple[str, dict]]:
    """Resolve the (strategy, params) plan for every column of a table.

    Lineage columns (prefixed ``_``) resolve to ``passthrough`` to mirror the engine's
    runtime behavior. Pure/metadata-only — does not touch any data value — so it is safe
    to use for audit manifests and coverage previews.
    """
    plan: dict[str, tuple[str, dict]] = {}
    for column in columns:
        if column.startswith("_"):
            plan[column] = ("passthrough", {})
        else:
            plan[column] = resolve_column_strategy(cfg, profile, table, column)
    return plan


# --------------------------------------------------------------------------------------
# Spark wrappers (only imported/used inside Fabric)
# --------------------------------------------------------------------------------------
def build_column_expr(column: str, strategy: str, params: dict, pepper: str):
    """Return a Spark Column expression that applies `strategy` to `column`.

    Lazily imports pyspark so this module stays importable for local unit tests.
    Values are processed row-wise via a UDF that calls the pure `apply_strategy`.
    """
    from pyspark.sql import functions as F  # type: ignore
    from pyspark.sql import types as T  # type: ignore

    # Result type depends on strategy; generalize->year returns int, date_shift returns date.
    if strategy == "generalize" and params.get("kind") in {"year", "birth_year"}:
        return_type: Any = T.IntegerType()
    elif strategy == "generalize" and params.get("kind") == "age_cap":
        return_type = T.IntegerType()
    elif strategy == "date_shift":
        return_type = T.DateType()
    else:
        return_type = T.StringType()

    # date_shift needs a per-row entity key; caller must pass entity_column in params.
    if strategy == "date_shift":
        entity_column = params.get("entity_column")
        if not entity_column:
            raise ValueError("date_shift requires params['entity_column'].")

        def _udf_fn(val, entity):  # noqa: ANN001
            p = dict(params)
            p["entity_value"] = entity
            return apply_strategy(val, strategy, p, pepper)

        udf = F.udf(_udf_fn, return_type)
        return udf(F.col(column), F.col(entity_column)).alias(column)

    def _udf_fn_single(val):  # noqa: ANN001
        return apply_strategy(val, strategy, params, pepper)

    udf = F.udf(_udf_fn_single, return_type)
    return udf(F.col(column)).alias(column)


def deidentify_table(
    df,
    cfg: dict,
    profile: str,
    table: str,
    pepper: str,
    *,
    runtime: dict[str, dict] | None = None,
):
    """Apply the profile's rules to every column of a Spark DataFrame.

    Columns explicitly configured get their strategy; everything else gets the
    profile default (suppress). Returns a new DataFrame. Never call .show()/.display()
    on the *input* df in the de-id notebook — see docs/security_model.md leak vectors.

    Parameters
    ----------
    runtime:
        ``{strategy_name: extra_params}`` merged over the rulebook's parameters for that
        strategy. This is the channel for state the rulebook *cannot* express because it
        does not exist until run time — specifically ``surrogate``'s ``crosswalk``, which
        is minted from the live population and passed as
        ``{"surrogate": {"crosswalk": mapping}}``.

        Keeping it out of ``cfg`` is deliberate: the rulebook is fingerprinted into every
        audit manifest, and a per-patient mapping has no business in an artifact that gets
        shared. The rulebook says *that* a column is surrogate-keyed; this says *which*
        codes. Runtime values win over rulebook values on key collision.
    """
    from pyspark.sql import functions as F  # type: ignore  # noqa: F401

    select_exprs = []
    for column in df.columns:
        # Preserve pipeline/lineage bookkeeping columns untouched.
        if column.startswith("_"):
            select_exprs.append(F.col(column))
            continue
        strategy, params = resolve_column_strategy(cfg, profile, table, column)
        if strategy == "passthrough":
            select_exprs.append(F.col(column))
        else:
            extra = (runtime or {}).get(strategy)
            if extra:
                params = {**params, **extra}
            select_exprs.append(build_column_expr(column, strategy, params, pepper))
    return df.select(*select_exprs)


# --------------------------------------------------------------------------------------
# Helpers + tiny synthetic wordlists (kept small; swap for Faker in production)
# --------------------------------------------------------------------------------------
def _coerce_date(value: Any) -> _dt.date | None:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return _dt.datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


_FIRST_NAMES = [
    "Alex",
    "Jordan",
    "Taylor",
    "Morgan",
    "Casey",
    "Riley",
    "Jamie",
    "Avery",
    "Quinn",
    "Skyler",
    "Cameron",
    "Reese",
    "Rowan",
    "Sawyer",
    "Emerson",
    "Finley",
]
_LAST_NAMES = [
    "Rivera",
    "Chen",
    "Patel",
    "Nguyen",
    "Kim",
    "Garcia",
    "Okafor",
    "Haddad",
    "Silva",
    "Novak",
    "Ivanov",
    "Costa",
    "Mbeki",
    "Andersson",
    "Rossi",
    "Dubois",
]
