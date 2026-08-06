"""The semantic model must stay in sync with the gold star it publishes.

Caveat 2 of this branch existed because nothing connected ``gold_conform`` to the TMDL
in ``reports/``: seven Clarity gold tables were written to the lakehouse every run and
none of them were in the semantic model, so Power BI simply could not see half the
accelerator's output. Nothing failed -- the notebooks were green, the tables were there,
the report just quietly showed the Caboodle half.

These tests make that failure mode loud. Add a table to ``CLARITY_GOLD`` without
re-running the generator, rename a column, or delete a relationship, and the build
breaks here instead of in a customer's report.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from fabric_phi_deid import gold_conform as gc

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = REPO_ROOT / "reports" / "Gold Safe Analytics.SemanticModel" / "definition"
TABLES_DIR = MODEL_DIR / "tables"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import generate_clarity_semantic_tables as gen  # noqa: E402


def tmdl(table: str) -> str:
    return (TABLES_DIR / f"gold_safe_{table}.tmdl").read_text(encoding="utf-8")


def declared_columns(text: str) -> list[str]:
    return re.findall(r"^\tcolumn (\S+)$", text, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# Coverage: every published table is visible to Power BI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", gc.GOLD_TABLES)
def test_every_gold_table_has_a_tmdl_file(table):
    path = TABLES_DIR / f"gold_safe_{table}.tmdl"
    assert path.is_file(), (
        f"{table} is published to gold but has no semantic model table, so it is "
        f"invisible in Power BI. Run scripts/generate_clarity_semantic_tables.py"
    )


@pytest.mark.parametrize("table", gc.GOLD_TABLES)
def test_every_gold_table_is_registered_in_model(table):
    model = (MODEL_DIR / "model.tmdl").read_text(encoding="utf-8")
    assert f"ref table gold_safe_{table}" in model, (
        f"gold_safe_{table}.tmdl exists but model.tmdl never references it, so the "
        f"table is not loaded. TMDL only picks up tables listed as `ref table`."
    )


# ---------------------------------------------------------------------------
# Fidelity: the model binds to the columns gold actually writes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table,spec", sorted(gc.CLARITY_GOLD.items()))
def test_clarity_columns_match_the_declaration(table, spec):
    assert declared_columns(tmdl(table)) == list(spec.published_columns), (
        f"gold_safe_{table} column list has drifted from CLARITY_GOLD. DirectLake binds "
        f"by name, so a stale column here fails at refresh, not at build."
    )


def test_dim_patient_covers_every_published_patient_column():
    missing = set(gc.PATIENT_GOLD_COLUMNS) - set(declared_columns(tmdl("dim_patient")))
    assert not missing, (
        f"gold_safe_dim_patient is missing {sorted(missing)}. The conformed patient "
        f"dimension is the whole point of the two-source star -- a column dropped here "
        f"is a column no report can reach."
    )


def test_source_system_is_exposed():
    """The one column that proves the two sources were conformed."""
    assert gc.SOURCE_SYSTEM_COLUMN in declared_columns(tmdl("dim_patient"))


def test_patient_key_is_the_declared_key():
    text = tmdl("dim_patient")
    block = text.split(f"\tcolumn {gc.PATIENT_KEY}\n", 1)[1].split("\n\n", 1)[0]
    assert "isKey" in block, (
        "PatientKey must be marked isKey; it is the 1-side of every fact relationship."
    )


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def relationships() -> list[tuple[str, str, str, str, str]]:
    text = (MODEL_DIR / "relationships.tmdl").read_text(encoding="utf-8")
    out = []
    for block in text.split("relationship ")[1:]:
        name = block.splitlines()[0].strip()
        frm = re.search(r"fromColumn: (\S+)\.(\S+)", block)
        to = re.search(r"toColumn: (\S+)\.(\S+)", block)
        assert frm and to, f"relationship {name} is missing an endpoint"
        out.append((name, frm.group(1), frm.group(2), to.group(1), to.group(2)))
    return out


def test_every_relationship_endpoint_exists():
    for name, ftab, fcol, ttab, tcol in relationships():
        for table, column in ((ftab, fcol), (ttab, tcol)):
            short = table.removeprefix("gold_safe_")
            path = TABLES_DIR / f"{table}.tmdl"
            assert path.is_file(), f"relationship {name} points at unknown table {table}"
            assert column in declared_columns(tmdl(short)), (
                f"relationship {name} points at {table}.{column}, which is not a column "
                f"of that table. Power BI drops such a relationship silently."
            )


@pytest.mark.parametrize("table", sorted(t for t in gc.CLARITY_GOLD if t.startswith("fact_")))
def test_every_clarity_fact_joins_the_conformed_patient(table):
    joined = {
        ftab
        for _, ftab, fcol, ttab, tcol in relationships()
        if ttab == "gold_safe_dim_patient" and tcol == gc.PATIENT_KEY and fcol == gc.PATIENT_KEY
    }
    assert f"gold_safe_{table}" in joined, (
        f"gold_safe_{table} has no relationship to the conformed patient dimension. "
        f"Without it the fact cannot be sliced by SourceSystem and the cross-source "
        f"story this star exists to tell is unreachable."
    )


def test_no_fact_to_fact_relationships():
    """Grain safety: facts conform through dimensions, never directly to each other."""
    offenders = [
        name for name, ftab, _, ttab, _ in relationships() if "fact_" in ftab and "fact_" in ttab
    ]
    assert not offenders, (
        f"fact-to-fact relationships {offenders} let filters cross grains and "
        f"double-count. Conform through a shared dimension instead."
    )


def test_clarity_keys_are_not_joined_to_caboodle_dimensions():
    """Clarity's DEPARTMENT_ID and CSN are a different key space, not conformed ones.

    Relating them to the Caboodle dimensions would fabricate conformance the extracts
    do not have and produce joins that look right and are wrong.
    """
    for name, ftab, _fcol, ttab, _ in relationships():
        if "clarity" in ftab and "clarity" not in ttab:
            assert ttab == "gold_safe_dim_patient", (
                f"{name} joins Clarity fact {ftab} to {ttab}. Patient is the only "
                f"dimension conformed across the two sources."
            )


# ---------------------------------------------------------------------------
# The generator is the source of truth
# ---------------------------------------------------------------------------


def test_generated_files_are_up_to_date():
    stale = [
        path.relative_to(REPO_ROOT).as_posix()
        for path, content in gen.build().items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    assert not stale, (
        f"{stale} no longer match gold_conform. "
        f"Run: python scripts/generate_clarity_semantic_tables.py"
    )


def test_generator_is_deterministic():
    """Regeneration must not churn lineageTags, which report visuals bind to."""
    assert gen.build() == gen.build()


def test_tokenised_identifiers_are_never_summarised():
    """A tokenised ID that Power BI decides to SUM is a nonsense number in a demo."""
    for table, spec in gc.CLARITY_GOLD.items():
        text = tmdl(table)
        for column in spec.published_columns:
            block = text.split(f"\tcolumn {column}\n", 1)[1].split("\n\n", 1)[0]
            assert "summarizeBy: none" in block, f"{table}.{column} must not auto-aggregate"
