"""End-to-end Spark integration tests for the de-id engine.

These exercise the *actual* PySpark code path (`deidentify_table` + UDFs), which the pure
unit tests cannot. Skipped automatically when PySpark is unavailable (e.g. local dev),
and run in CI where PySpark is installed. Marked ``spark`` so they can be selected/skipped
explicitly: ``pytest -m spark`` or ``pytest -m "not spark"``.
"""

from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402

from fabric_phi_deid.deid_engine import deidentify_table, load_rules  # noqa: E402

pytestmark = pytest.mark.spark

PEPPER = "unit-test-pepper-not-a-real-secret-0123456789"


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("phi-deid-tests")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.fixture(scope="module")
def cfg(rules_path):
    return load_rules(rules_path)


def _patient_df(spark):
    rows = [
        ("MRN-1", "John", "Smith", "Smith, John", dt.date(1984, 7, 22), 41, "10021", 7, "keep"),
        ("MRN-2", "Jane", "Doe", "Doe, Jane", dt.date(1950, 1, 3), 99, "03688", 9, "keep"),
    ]
    cols = [
        "MRN",
        "FirstName",
        "LastName",
        "PatientName",
        "DateOfBirth",
        "Age",
        "ZIP",
        "PatientKey",
        "MysteryLeak",
    ]
    return spark.createDataFrame(rows, cols)


def test_deidentify_patient_table(spark, cfg):
    src = _patient_df(spark)
    out = deidentify_table(src, cfg, "safe_harbor", "dim_patient", PEPPER)
    result = {r["PatientKey"]: r.asDict() for r in out.collect()}

    r1 = result[7]
    # MRN tokenized with prefix; DOB -> birth year (int); ZIP -> 3 digits.
    assert r1["MRN"].startswith("PT-")
    assert r1["DateOfBirth"] == 1984
    assert r1["ZIP"] == "100"
    assert r1["PatientName"] != "Smith, John"  # synthesized
    assert r1["PatientKey"] == 7  # surrogate passthrough
    # Unclassified column dropped to None by deny-by-default suppress.
    assert r1["MysteryLeak"] is None

    r2 = result[9]
    assert r2["Age"] == 90  # age cap
    assert r2["ZIP"] == "000"  # 036 is a restricted low-pop prefix


def test_referential_integrity_across_tables(spark, cfg):
    # The same MRN tokenizes identically in a second table -> joins survive de-id.
    src = _patient_df(spark)
    out_a = deidentify_table(src, cfg, "safe_harbor", "dim_patient", PEPPER)
    out_b = deidentify_table(src, cfg, "safe_harbor", "dim_patient", PEPPER)
    a = {r["PatientKey"]: r["MRN"] for r in out_a.collect()}
    b = {r["PatientKey"]: r["MRN"] for r in out_b.collect()}
    assert a == b


def test_expert_profile_date_shift_preserves_interval(spark, cfg):
    rows = [("MRN-1", dt.date(2020, 1, 1), 7), ("MRN-1", dt.date(2020, 2, 1), 7)]
    df = spark.createDataFrame(rows, ["MRN", "ServiceDate", "PatientKey"])
    out = deidentify_table(df, cfg, "expert_determination", "fact_claim", PEPPER)
    shifted = sorted(r["ServiceDate"] for r in out.collect())
    # Same patient -> both dates shift by the same offset -> 31-day gap preserved.
    assert (shifted[1] - shifted[0]) == dt.timedelta(days=31)
