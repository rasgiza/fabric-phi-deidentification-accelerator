"""fabric_phi_deid — config-driven PHI de-identification + tokenization for Microsoft Fabric.

SYNTHETIC-DATA-ONLY reference pattern. This package is **not** a certified de-identification
service. Real-PHI use requires an independent HIPAA §164.514(b) expert-determination
sign-off (see docs/pre_real_phi_checklist.md).

Public API
----------
- Tokenization:      tokenize, tokenize_numeric, tokenize_format_preserving, get_pepper
- Strategy engine:   apply_strategy, load_rules, resolve_column_strategy, deidentify_table
- Config integrity:  validate_config, audit_coverage, ConfigValidationError
- Audit:             config_fingerprint, RunManifest, build_run_manifest, get_audit_logger
- Validation:        scan_value_for_phi, PHI_PATTERNS
- Privacy metrics:   measure_k_anonymity, measure_l_diversity, measure_t_closeness,
                     enforce_k_anonymity
- Free-text NER:     analyze_text, redact_text, scan_texts, NER_AVAILABLE
- Eval harness:      ClassificationMetrics, evaluate_sets, evaluate_spans
"""

from __future__ import annotations

__version__ = "0.1.0"

from .audit import (
    RunManifest,
    build_run_manifest,
    config_fingerprint,
    get_audit_logger,
    write_manifest,
)
from .config import (
    ConfigValidationError,
    CoverageReport,
    audit_coverage,
    validate_config,
)
from .deid_engine import (
    STRATEGIES,
    apply_strategy,
    deidentify_table,
    load_rules,
    resolve_column_strategy,
    resolve_table_plan,
)
from .determination import (
    DeterminationReport,
    ResidualScanResult,
    build_determination_report,
    residual_scan_from_hits,
)
from .eval_harness import (
    ClassificationMetrics,
    GoldSpan,
    evaluate_flags,
    evaluate_sets,
    evaluate_spans,
)
from .ner_text import (
    NER_AVAILABLE,
    TextFinding,
    analyze_text,
    redact_text,
    scan_texts,
)
from .privacy_metrics import (
    KAnonymityReport,
    LDiversityReport,
    SuppressionReport,
    TClosenessReport,
    enforce_k_anonymity,
    measure_k_anonymity,
    measure_l_diversity,
    measure_t_closeness,
)
from .tokenization import (
    get_pepper,
    tokenize,
    tokenize_format_preserving,
    tokenize_numeric,
)
from .validation import PHI_PATTERNS, scan_value_for_phi

__all__ = [
    "__version__",
    # tokenization
    "tokenize",
    "tokenize_numeric",
    "tokenize_format_preserving",
    "get_pepper",
    # engine
    "apply_strategy",
    "load_rules",
    "resolve_column_strategy",
    "resolve_table_plan",
    "deidentify_table",
    "STRATEGIES",
    # config integrity
    "validate_config",
    "audit_coverage",
    "ConfigValidationError",
    "CoverageReport",
    # audit
    "config_fingerprint",
    "RunManifest",
    "build_run_manifest",
    "write_manifest",
    "get_audit_logger",
    # validation
    "scan_value_for_phi",
    "PHI_PATTERNS",
    # privacy metrics
    "measure_k_anonymity",
    "measure_l_diversity",
    "measure_t_closeness",
    "enforce_k_anonymity",
    "KAnonymityReport",
    "LDiversityReport",
    "TClosenessReport",
    "SuppressionReport",
    # expert determination evidence pack
    "DeterminationReport",
    "ResidualScanResult",
    "build_determination_report",
    "residual_scan_from_hits",
    # free-text NER
    "analyze_text",
    "redact_text",
    "scan_texts",
    "TextFinding",
    "NER_AVAILABLE",
    # eval harness
    "ClassificationMetrics",
    "GoldSpan",
    "evaluate_sets",
    "evaluate_flags",
    "evaluate_spans",
]
