"""The bronze schemas declared in 01_bronze_ingest must match the sample CSVs exactly.

``ingest_file()`` enforces a HEADER CONTRACT at run time: if a file's columns do not equal
the declared schema, in order, it raises rather than parsing positionally. That check is
excellent -- and it only fires inside Fabric, on a live Spark session, after someone has
uploaded the data. By then a schema/data mismatch has already cost a pipeline run.

This test moves the same contract into CI, where it costs milliseconds. It matters most for
the identifier columns: a de-identification accelerator whose ingest silently disagrees with
its own sample data cannot demonstrate that it removes anything.

The schemas are extracted by executing ONLY the schema-definition region of the notebook
against a stub ``T`` (pyspark is deliberately not a test dependency), so the test reads the
notebook's real source rather than a copy that could drift from it.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = REPO_ROOT / "notebooks" / "01_bronze_ingest.ipynb"
SAMPLE_DATA = REPO_ROOT / "sample_data"

# Mirrors the SOURCES registry in the notebook: dict name -> directory holding the CSVs.
SOURCE_DIRS = {
    "CABOODLE_SCHEMAS": SAMPLE_DATA / "caboodle_provider",
    "CLARITY_SCHEMAS": SAMPLE_DATA / "Clarity",
}

REGION_START = "S = T.StructType"
REGION_END = "# ---- source registry"

pytestmark = pytest.mark.skipif(
    not SAMPLE_DATA.is_dir(),
    reason="sample_data/ not present (generate it with scripts/generate_sample_data.py)",
)


class _StubTypes:
    """Just enough of ``pyspark.sql.types`` to evaluate the schema literals.

    ``StructType`` collapses to the list of column NAMES, which is precisely what the
    header contract compares. Column types are irrelevant here and are discarded.
    """

    @staticmethod
    def StructType(fields):  # noqa: N802 - mirrors the pyspark name being stubbed
        return list(fields)

    @staticmethod
    def StructField(name, dataType, nullable=True):  # noqa: N802,N803 - ditto
        return name

    @staticmethod
    def IntegerType():  # noqa: N802
        return None

    @staticmethod
    def StringType():  # noqa: N802
        return None

    @staticmethod
    def DoubleType():  # noqa: N802
        return None

    @staticmethod
    def BooleanType():  # noqa: N802
        return None

    @staticmethod
    def DateType():  # noqa: N802
        return None

    @staticmethod
    def TimestampType():  # noqa: N802
        return None

    @staticmethod
    def DecimalType(precision=10, scale=0):  # noqa: N802
        return None


def _declared_schemas() -> dict[str, dict[str, list[str]]]:
    """Extract ``{dict_name: {table: [column, ...]}}`` from the notebook source."""
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    source = "\n".join(
        "".join(cell["source"]) for cell in nb["cells"] if cell["cell_type"] == "code"
    )
    start = source.index(REGION_START)
    end = source.index(REGION_END, start)
    namespace: dict[str, object] = {"T": _StubTypes}
    exec(compile(source[start:end], "01_bronze_ingest.schemas", "exec"), namespace)  # noqa: S102
    return {name: namespace[name] for name in SOURCE_DIRS}  # type: ignore[misc]


def _csv_header(path: Path) -> list[str]:
    with open(path, encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


SCHEMAS = _declared_schemas() if NOTEBOOK.is_file() and SAMPLE_DATA.is_dir() else {}

CASES = [
    (dict_name, table, columns)
    for dict_name, tables in SCHEMAS.items()
    for table, columns in tables.items()
]


@pytest.mark.parametrize(
    ("dict_name", "table", "columns"),
    CASES,
    ids=[f"{d.split('_')[0].lower()}:{t}" for d, t, _ in CASES],
)
def test_declared_schema_matches_csv_header(dict_name: str, table: str, columns: list[str]) -> None:
    """Every declared bronze schema matches its CSV header exactly, in order."""
    path = SOURCE_DIRS[dict_name] / f"{table}.csv"
    assert path.is_file(), (
        f"{dict_name} declares {table}, but {path.relative_to(REPO_ROOT)} does not exist. "
        "01_bronze_ingest ingests every declared table, so this run would fail in Fabric."
    )
    assert _csv_header(path) == columns, (
        f"{table}.csv header does not match the schema declared in 01_bronze_ingest.\n"
        f"  declared: {columns}\n"
        f"  actual:   {_csv_header(path)}\n"
        "The notebook parses CSVs POSITIONALLY under this schema -- a mismatch would route "
        "one column's values through another column's de-identification rule."
    )


def test_every_sample_csv_is_declared() -> None:
    """No CSV sits in sample_data/ unclaimed by a bronze schema.

    The reverse of the check above, and the one that actually catches a coverage gap: a file
    full of identifiers that no schema declares is never ingested, never de-identified, and
    never scanned -- so every downstream control passes without ever having seen it.
    """
    undeclared = []
    for dict_name, directory in SOURCE_DIRS.items():
        if not directory.is_dir():
            continue
        declared = set(SCHEMAS.get(dict_name, {}))
        for path in sorted(directory.glob("*.csv")):
            if path.stem not in declared:
                undeclared.append(f"{path.relative_to(REPO_ROOT)}")
    assert not undeclared, (
        "These sample CSVs are not declared in any 01_bronze_ingest schema, so the pipeline "
        f"never sees them: {undeclared}. Add a schema or delete the file."
    )
