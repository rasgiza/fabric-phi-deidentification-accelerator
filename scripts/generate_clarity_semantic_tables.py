"""Generate the Clarity half of the Gold Safe Analytics semantic model.

Why this is generated and not hand-authored
-------------------------------------------
The gold star is declared once, in :mod:`fabric_phi_deid.gold_conform`. Every consumer
of that declaration -- the two gold notebooks, the scorecard, the cleanup notebook --
reads it at runtime, so they cannot drift. The semantic model was the one consumer that
could: it is static TMDL sitting in ``reports/``, and nothing connected it back to the
declaration. That is exactly how the seven Clarity gold tables came to be published to
the lakehouse but invisible in Power BI.

This script closes that gap. It renders one ``.tmdl`` file per Clarity gold table from
``CLARITY_GOLD``, wires the relationships, and registers the tables in ``model.tmdl``.
Re-run it after changing ``CLARITY_GOLD`` and the model follows. ``tests/test_semantic_model.py``
asserts the rendered output is in sync, so forgetting to re-run it fails the build
rather than silently shipping a model that is missing a fact table.

Determinism
-----------
TMDL requires a ``lineageTag`` GUID on every table, column and measure. Random GUIDs
would make every regeneration a large meaningless diff and would break report bindings
that reference them. Tags are therefore derived with ``uuid5`` from a fixed namespace
plus the object's path, so the same model always renders byte-identically.

Usage
-----
    python scripts/generate_clarity_semantic_tables.py           # write the files
    python scripts/generate_clarity_semantic_tables.py --check   # verify, change nothing
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fabric_phi_deid import gold_conform as gc  # noqa: E402

MODEL_DIR = REPO_ROOT / "reports" / "Gold Safe Analytics.SemanticModel" / "definition"
TABLES_DIR = MODEL_DIR / "tables"

GOLD_PREFIX = "gold_safe_"
SCHEMA_NAME = "dbo"
EXPRESSION_SOURCE = "DatabaseQuery"

# Stable seed for uuid5. Changing this rewrites every lineageTag in the generated files,
# so it is pinned and must not be edited.
LINEAGE_NAMESPACE = uuid.UUID("6f1b6a3e-9d2f-5b7a-8c4d-1e0a2b3c4d5e")

# ---------------------------------------------------------------------------
# Column typing
# ---------------------------------------------------------------------------
# De-identification turns every identifier into a token, so the overwhelming default in
# the Clarity gold tables is `string`. Only genuine measures and counters stay numeric,
# and they are named here rather than inferred, because guessing wrong would let Power BI
# silently SUM a tokenised identifier.
INT64_COLUMNS = frozenset({"LINE", "QUANTITY", "LOS_DAYS"})
DOUBLE_COLUMNS = frozenset({"HV_DISCRETE_DOSE", "ORD_NUM_VALUE"})

# Keys are hidden: surrogate keys and foreign keys are join plumbing, not analysis
# columns. Degenerate business keys (order IDs, encounter CSNs) stay visible because
# distinct-counting them is a legitimate question.
HIDDEN_COLUMNS = frozenset(
    {
        gc.PATIENT_KEY,
        gc.CLARITY_PATIENT_LINK,  # PAT_ID -- redundant with dim_patient[ClarityPatientID]
        "VISIT_PROV_ID",
        "ADMISSION_PROV_ID",
        "AUTHRZING_PROV_ID",
    }
)

# The 1-side of each relationship needs a declared key.
KEY_COLUMNS = {"dim_clarity_provider": "PROV_ID"}

# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------
# Deliberately conservative: a plain row count per fact, plus average length of stay,
# which is the one column in the Clarity set that is unambiguously additive-by-average.
# Nothing here depends on a coded value whose domain is not pinned in the rulebook.
MEASURES: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "fact_clarity_encounter": (("Clarity Encounters", "COUNTROWS ( {table} )", "#,0", "KPIs"),),
    "fact_clarity_admission": (
        ("Clarity Admissions", "COUNTROWS ( {table} )", "#,0", "KPIs"),
        ("Avg Length of Stay", "AVERAGE ( {table}[LOS_DAYS] )", "0.0", "KPIs"),
    ),
    "fact_clarity_diagnosis": (("Clarity Diagnoses", "COUNTROWS ( {table} )", "#,0", "KPIs"),),
    "fact_clarity_order_med": (
        ("Clarity Medication Orders", "COUNTROWS ( {table} )", "#,0", "KPIs"),
    ),
    "fact_clarity_order_proc": (
        ("Clarity Procedure Orders", "COUNTROWS ( {table} )", "#,0", "KPIs"),
    ),
    "fact_clarity_result": (("Clarity Results", "COUNTROWS ( {table} )", "#,0", "KPIs"),),
}

# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
# Patient is the ONLY conformed dimension across the two sources -- that is the whole
# point of the conformed star, and it is why every Clarity fact joins to the same
# gold_safe_dim_patient the Caboodle facts use.
#
# Clarity's DEPARTMENT_ID and PAT_ENC_CSN_ID are deliberately NOT related to
# gold_safe_dim_department or gold_safe_fact_encounter. Those are different key spaces
# that happen to describe similar things; joining them would fabricate conformance the
# extracts do not have. Same reasoning keeps fact_clarity_result unrelated to
# fact_clarity_order_proc: a fact-to-fact relationship would let filters propagate
# across grains and double-count.
CLARITY_PROVIDER_TABLE = "dim_clarity_provider"
PATIENT_TABLE = "dim_patient"

PROVIDER_LINKS: dict[str, str] = {
    "fact_clarity_encounter": "VISIT_PROV_ID",
    "fact_clarity_admission": "ADMISSION_PROV_ID",
    "fact_clarity_order_med": "AUTHRZING_PROV_ID",
    "fact_clarity_order_proc": "AUTHRZING_PROV_ID",
}

RELATIONSHIP_SLUGS = {
    "fact_clarity_encounter": "enc",
    "fact_clarity_admission": "adm",
    "fact_clarity_diagnosis": "dx",
    "fact_clarity_order_med": "med",
    "fact_clarity_order_proc": "proc",
    "fact_clarity_result": "res",
}

MARKER_BEGIN = "/// BEGIN generated by scripts/generate_clarity_semantic_tables.py"
MARKER_END = "/// END generated by scripts/generate_clarity_semantic_tables.py"


def gold_name(table: str) -> str:
    return f"{GOLD_PREFIX}{table}"


def lineage(*parts: str) -> str:
    return str(uuid.uuid5(LINEAGE_NAMESPACE, "/".join(parts)))


def data_type(column: str) -> str:
    if column == gc.PATIENT_KEY or column in INT64_COLUMNS:
        return "int64"
    if column in DOUBLE_COLUMNS:
        return "double"
    # Year columns are generalised dates -- always whole numbers.
    if column.endswith("Year"):
        return "int64"
    return "string"


def render_table(table: str, spec: gc.GoldTable) -> str:
    name = gold_name(table)
    lines = [f"table {name}", f"\tlineageTag: {lineage(name)}", ""]

    for measure, expr, fmt, folder in MEASURES.get(table, ()):
        lines += [
            f"\tmeasure '{measure}' = {expr.format(table=name)}",
            f"\t\tformatString: {fmt}",
            f"\t\tdisplayFolder: {folder}",
            f"\t\tlineageTag: {lineage(name, 'measure', measure)}",
            "",
        ]

    for column in spec.published_columns:
        lines.append(f"\tcolumn {column}")
        lines.append(f"\t\tdataType: {data_type(column)}")
        if column in HIDDEN_COLUMNS:
            lines.append("\t\tisHidden")
        if KEY_COLUMNS.get(table) == column:
            lines.append("\t\tisKey")
        lines.append(f"\t\tlineageTag: {lineage(name, 'column', column)}")
        lines.append("\t\tsummarizeBy: none")
        lines.append(f"\t\tsourceColumn: {column}")
        lines.append("")

    lines += [
        f"\tpartition {name} = entity",
        "\t\tmode: directLake",
        "\t\tsource",
        f"\t\t\tentityName: {name}",
        f"\t\t\tschemaName: {SCHEMA_NAME}",
        f"\t\t\texpressionSource: {EXPRESSION_SOURCE}",
        "",
    ]
    return "\n".join(lines)


def render_relationships() -> str:
    lines: list[str] = [MARKER_BEGIN, ""]
    for table, slug in RELATIONSHIP_SLUGS.items():
        if table not in gc.CLARITY_GOLD:
            continue
        lines += [
            f"relationship sclr_{slug}_patient",
            f"\tfromColumn: {gold_name(table)}.{gc.PATIENT_KEY}",
            f"\ttoColumn: {gold_name(PATIENT_TABLE)}.{gc.PATIENT_KEY}",
            "",
        ]
        provider_column = PROVIDER_LINKS.get(table)
        if provider_column:
            lines += [
                f"relationship sclr_{slug}_provider",
                f"\tfromColumn: {gold_name(table)}.{provider_column}",
                f"\ttoColumn: {gold_name(CLARITY_PROVIDER_TABLE)}.PROV_ID",
                "",
            ]
    lines.append(MARKER_END)
    return "\n".join(lines)


def render_model_refs() -> str:
    refs = [f"ref table {gold_name(t)}" for t in gc.CLARITY_GOLD]
    return "\n".join([MARKER_BEGIN, "", *refs, "", MARKER_END])


def splice(text: str, block: str) -> str:
    """Replace the generated region of *text*, or append it if not present."""
    if MARKER_BEGIN in text and MARKER_END in text:
        head = text.split(MARKER_BEGIN)[0]
        tail = text.split(MARKER_END, 1)[1]
        return f"{head}{block}{tail}"
    return f"{text.rstrip()}\n\n{block}\n"


def build() -> dict[Path, str]:
    """Every file this generator owns, mapped to its intended content."""
    out: dict[Path, str] = {}
    for table, spec in gc.CLARITY_GOLD.items():
        out[TABLES_DIR / f"{gold_name(table)}.tmdl"] = render_table(table, spec)

    rel_path = MODEL_DIR / "relationships.tmdl"
    out[rel_path] = splice(rel_path.read_text(encoding="utf-8"), render_relationships())

    model_path = MODEL_DIR / "model.tmdl"
    out[model_path] = splice(model_path.read_text(encoding="utf-8"), render_model_refs())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report drift and exit non-zero; write nothing"
    )
    args = parser.parse_args()

    stale: list[Path] = []
    for path, content in build().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        rel = path.relative_to(REPO_ROOT)
        if current == content:
            print(f"  ok       {rel}")
            continue
        stale.append(path)
        if args.check:
            print(f"  DRIFT    {rel}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"  {'updated' if current else 'created'}  {rel}")

    if args.check and stale:
        print(f"\n{len(stale)} file(s) out of sync with gold_conform.CLARITY_GOLD.")
        print("Run: python scripts/generate_clarity_semantic_tables.py")
        return 1
    print(f"\n{len(gc.CLARITY_GOLD)} Clarity table(s) bound to the semantic model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
