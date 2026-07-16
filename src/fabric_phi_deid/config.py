"""
config.py — schema validation and coverage linting for deid_rules.yaml.

Two independent safety nets sit in front of the engine:

1. **validate_config** — structural/semantic correctness of the rulebook itself. Catches
   unknown strategies, invalid ``generalize`` kinds, a ``date_shift`` missing its
   ``entity_column``, a missing/unknown ``active_profile``, etc. ``load_rules`` calls this
   and refuses to run on an invalid config (fail-fast, before any data is touched).

2. **audit_coverage** — compares the rulebook against the ACTUAL columns of a table. It
   surfaces two silent-failure classes that deny-by-default alone cannot:
     - ``defaulted`` : a real column with no explicit rule -> it will be SUPPRESSED. Safe
       from a leakage standpoint, but you may have *meant* to tokenize it (breaking joins
       silently). These deserve a human's eyes.
     - ``missing``   : a rule that references a column NOT present in the data -> almost
       always a typo or schema drift. The rule is dead and the real column (if renamed)
       may be falling to default.

The set of valid strategies + their parameter contracts is declared here as
``STRATEGY_SPECS``. A drift test (tests/test_config_validation.py) asserts these keys stay
in lock-step with the engine's ``STRATEGIES`` registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "STRATEGY_SPECS",
    "ConfigValidationError",
    "CoverageReport",
    "validate_config",
    "audit_coverage",
]


# Parameter contract per strategy. ``required`` params must be present; ``enum`` restricts
# a param to a fixed set of values. Anything not listed is allowed but ignored.
STRATEGY_SPECS: dict[str, dict[str, Any]] = {
    "passthrough": {"required": [], "enums": {}},
    "suppress": {"required": [], "enums": {}},
    "tokenize": {"required": [], "enums": {}},
    "generalize": {"required": ["kind"], "enums": {"kind": {"year", "zip3", "age_cap"}}},
    "date_shift": {"required": ["entity_column"], "enums": {}},
    "synthesize": {
        "required": ["kind"],
        "enums": {"kind": {"first_name", "last_name", "name"}},
    },
    "redact_text": {
        "required": [],
        "enums": {
            "replacement": {"label", "token", "remove"},
            "backend": {"auto", "presidio", "regex"},
        },
    },
}


class ConfigValidationError(ValueError):
    """Raised when deid_rules.yaml fails structural/semantic validation.

    Carries the full list of problems so the operator can fix them in one pass.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        joined = "\n  - ".join(errors)
        super().__init__(f"Invalid de-id config ({len(errors)} problem(s)):\n  - {joined}")


def _validate_column_rule(
    profile: str, table: str, column: str, rule: Any, errors: list[str]
) -> None:
    """Validate a single column rule (string shorthand or dict full-form)."""
    where = f"profiles.{profile}.tables.{table}.{column}"

    strategy: str
    params: dict[str, Any]
    if isinstance(rule, str):
        strategy = rule
        params = {}
    elif isinstance(rule, dict):
        raw_strategy = rule.get("strategy")
        if not raw_strategy:
            errors.append(f"{where}: dict rule is missing required 'strategy' key.")
            return
        strategy = raw_strategy
        params = {k: v for k, v in rule.items() if k != "strategy"}
    else:
        errors.append(f"{where}: rule must be a string or a mapping, got {type(rule).__name__}.")
        return

    spec = STRATEGY_SPECS.get(strategy)
    if spec is None:
        errors.append(f"{where}: unknown strategy {strategy!r}. Valid: {sorted(STRATEGY_SPECS)}.")
        return

    for req in spec["required"]:
        if req not in params:
            errors.append(f"{where}: strategy {strategy!r} requires param {req!r}.")

    for param, allowed in spec["enums"].items():
        if param in params and params[param] not in allowed:
            errors.append(
                f"{where}: {param}={params[param]!r} is invalid for {strategy!r}; "
                f"allowed: {sorted(allowed)}."
            )


def validate_config(cfg: Any) -> list[str]:
    """Return a list of validation errors for a loaded config (empty list == valid)."""
    errors: list[str] = []

    if not isinstance(cfg, dict):
        return ["Config root must be a mapping."]

    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        return ["Config must define a non-empty 'profiles' mapping."]

    active = cfg.get("active_profile")
    if active is not None and active not in profiles:
        errors.append(f"active_profile {active!r} is not defined in profiles ({sorted(profiles)}).")

    for profile, prof in profiles.items():
        if not isinstance(prof, dict):
            errors.append(f"profiles.{profile}: must be a mapping.")
            continue

        default_strategy = prof.get("default_strategy", "suppress")
        if default_strategy not in STRATEGY_SPECS:
            errors.append(
                f"profiles.{profile}.default_strategy: unknown strategy "
                f"{default_strategy!r}. Valid: {sorted(STRATEGY_SPECS)}."
            )

        tables = prof.get("tables", {}) or {}
        if not isinstance(tables, dict):
            errors.append(f"profiles.{profile}.tables: must be a mapping.")
            continue

        for table, table_rules in tables.items():
            if not isinstance(table_rules, dict):
                errors.append(f"profiles.{profile}.tables.{table}: must be a mapping.")
                continue
            for column, rule in table_rules.items():
                _validate_column_rule(profile, table, column, rule, errors)

    return errors


@dataclass
class CoverageReport:
    """Result of comparing a profile's table rules against actual table columns."""

    profile: str
    table: str
    default_strategy: str
    classified: list[str] = field(default_factory=list)
    defaulted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True when every actual column is explicitly classified and no rule is dead."""
        return not self.defaulted and not self.missing

    def summary(self) -> str:
        parts = [
            f"[{self.profile}/{self.table}] classified={len(self.classified)}",
            f"defaulted={len(self.defaulted)} (-> {self.default_strategy})",
            f"missing={len(self.missing)}",
        ]
        return " ".join(parts)


def audit_coverage(cfg: dict, profile: str, table: str, columns: list[str]) -> CoverageReport:
    """Compare the rulebook for (profile, table) against actual ``columns``.

    Lineage/bookkeeping columns (prefixed ``_``) are ignored — the engine passes them
    through untouched by design.
    """
    if profile not in cfg.get("profiles", {}):
        raise ValueError(f"Unknown profile {profile!r}.")
    prof = cfg["profiles"][profile]
    default_strategy = prof.get("default_strategy", "suppress")
    table_rules = (prof.get("tables", {}) or {}).get(table, {}) or {}

    ruled = set(table_rules.keys())
    actual = [c for c in columns if not c.startswith("_")]
    actual_set = set(actual)

    classified = sorted(c for c in actual if c in ruled)
    defaulted = sorted(c for c in actual if c not in ruled)
    missing = sorted(ruled - actual_set)

    return CoverageReport(
        profile=profile,
        table=table,
        default_strategy=default_strategy,
        classified=classified,
        defaulted=defaulted,
        missing=missing,
    )
