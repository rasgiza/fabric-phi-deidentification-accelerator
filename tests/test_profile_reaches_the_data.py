"""Does the rulebook actually reach the columns that carry identifiers?

Three earlier defects in this accelerator were all the same shape: something looked green
because it was never asked a question it could fail. The sample data held no SSNs, so the
residual-PHI scan passed. The profile named a column that silver had renamed, so the rule
matched nothing. A ``synthesize`` rule emitted plausible names, so nobody noticed the
originals were still derivable.

This module attacks that shape directly. It does not check the *engine* -- ``test_deid_
engine.py`` does that -- it checks the seam between the sample estate and the rulebook,
which is where a new column silently becomes an unruled column and inherits a default
nobody consciously chose.

There is no Spark here on purpose: this is pure rule resolution, so it runs in
milliseconds and can never be skipped for being slow.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fabric_phi_deid.deid_engine import load_rules, resolve_column_strategy

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "sample_data"
CONFIG = REPO / "config" / "deid_rules.yaml"

pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(), reason="sample_data/ not present in this checkout"
)

#: Sample CSV -> the silver table name the profile addresses it by. Only the identifier-
#: bearing tables are listed; reference/lookup tables carry no PHI and are copied verbatim.
CSV_TO_TABLE: dict[str, str] = {
    "caboodle_provider/DimPatient.csv": "dim_patient",
    "caboodle_provider/DimHospitalAccount.csv": "dim_hospital_account",
    "caboodle_provider/FactPatientDevice.csv": "fact_patient_device",
    "caboodle_provider/FactPortalAccess.csv": "fact_portal_access",
    "caboodle_provider/FactPatientTransport.csv": "fact_patient_transport",
    "Clarity/PATIENT.csv": "clarity_patient",
    "Clarity/COVERAGE.csv": "clarity_coverage",
    "Clarity/HSP_ACCOUNT.csv": "clarity_hsp_account",
}

#: (table, column) pairs that are legitimately passed through even though the identifier
#: inventory points at them. Each one needs a reason, and the reason is the test.
PASSTHROUGH_BY_DESIGN: dict[tuple[str, str], str] = {
    # A warehouse surrogate assigned by the ETL, carrying no information about the person.
    # That is the §164.514(c) argument, and it is the same argument the `surrogate` strategy
    # makes -- the difference is only that this key was assigned upstream of us.
    ("dim_patient", "PatientKey"): "warehouse surrogate, assigned not derived",
}

#: Every column that holds one of the 18 identifiers, as (table, column). Kept separate
#: from IDENTIFIER_HOMES in test_sample_data_identifiers.py because that module asks
#: "is the identifier present in the sample data?" and this one asks "is it ruled?".
IDENTIFIER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("dim_patient", "PatientName"),
    ("dim_patient", "FirstName"),
    ("dim_patient", "LastName"),
    ("dim_patient", "MRN"),
    ("dim_patient", "DateOfBirth"),
    ("dim_patient", "AddressLine1"),
    ("dim_patient", "City"),
    ("dim_patient", "ZIP"),
    ("dim_patient", "HomePhone"),
    ("dim_patient", "MobilePhone"),
    ("dim_patient", "Email"),
    ("dim_patient", "SSN"),
    ("dim_patient", "DriversLicenseNumber"),
    ("dim_patient", "HealthPlanMemberID"),
    ("dim_patient", "BiometricTemplateID"),
    ("dim_patient", "FacePhotoURI"),
    ("dim_hospital_account", "HospitalAccountNumber"),
    ("dim_hospital_account", "GuarantorAccountNumber"),
    ("dim_hospital_account", "GuarantorName"),
    ("dim_hospital_account", "GuarantorPhone"),
    ("dim_hospital_account", "GuarantorFaxNumber"),
    ("fact_patient_device", "SerialNumber"),
    ("fact_patient_device", "UDI"),
    ("fact_portal_access", "PortalUserName"),
    ("fact_portal_access", "SourceIPAddress"),
    ("fact_portal_access", "AccessedURL"),
    ("fact_portal_access", "SessionID"),
    ("fact_patient_transport", "VehicleIdentificationNumber"),
    ("fact_patient_transport", "LicensePlateNumber"),
    ("clarity_patient", "PAT_NAME"),
    ("clarity_patient", "PAT_MRN_ID"),
    ("clarity_patient", "SSN"),
    ("clarity_patient", "HOME_PHONE"),
    ("clarity_patient", "WORK_PHONE"),
    ("clarity_patient", "MOBILE_PHONE"),
    ("clarity_patient", "EMAIL_ADDRESS"),
    ("clarity_patient", "ADD_LINE_1"),
    ("clarity_coverage", "SUBSCRIBER_ID"),
    ("clarity_coverage", "GROUP_NUM"),
    ("clarity_coverage", "SUBSCRIBER_NAME"),
    ("clarity_hsp_account", "ACCT_BILLING_NUM"),
    ("clarity_hsp_account", "GUAR_NAME"),
    ("clarity_hsp_account", "GUAR_HOME_PHONE"),
    ("clarity_hsp_account", "GUAR_FAX"),
)


@pytest.fixture(scope="module")
def cfg() -> dict:
    return load_rules(CONFIG)


@pytest.fixture(scope="module")
def profiles(cfg: dict) -> list[str]:
    return sorted(cfg["profiles"])


def _header(rel: str) -> list[str]:
    with open(SAMPLE / rel, encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


def _table_columns(cfg: dict, profile: str, table: str) -> dict:
    return (cfg["profiles"][profile].get("tables") or {}).get(table) or {}


@pytest.mark.parametrize(("rel", "table"), sorted(CSV_TO_TABLE.items()))
def test_every_source_column_has_an_explicit_rule(cfg: dict, rel: str, table: str) -> None:
    """Deny-by-default is a safety net, not a policy.

    An unruled column is suppressed, which is safe -- and invisible. The column silently
    stops arriving, someone notices a broken report months later, and the fix is applied
    downstream instead of in the rulebook. Worse, nobody ever *decided* anything about it.
    Every column a source system actually ships must be a deliberate entry.
    """
    columns = [c for c in _header(rel) if not c.startswith("_")]
    for profile in sorted(cfg["profiles"]):
        rules = _table_columns(cfg, profile, table)
        if not rules:
            continue  # profile does not cover this table at all; scope is tested elsewhere
        unruled = [c for c in columns if c not in rules]
        assert not unruled, (
            f"profile {profile!r} has no rule for {table}.{unruled} -- these columns exist "
            f"in {rel} and would be silently suppressed by default_strategy"
        )


@pytest.mark.parametrize(("table", "column"), IDENTIFIER_COLUMNS)
def test_identifier_columns_are_never_passed_through(
    cfg: dict, profiles: list[str], table: str, column: str
) -> None:
    """The single assertion this whole accelerator exists to make."""
    if (table, column) in PASSTHROUGH_BY_DESIGN:
        pytest.skip(PASSTHROUGH_BY_DESIGN[(table, column)])
    for profile in profiles:
        if not _table_columns(cfg, profile, table):
            continue
        strategy, _ = resolve_column_strategy(cfg, profile, table, column)
        assert strategy != "passthrough", (
            f"{profile}/{table}.{column} is passed through unchanged, but it carries a "
            "HIPAA §164.514(b)(2)(i) identifier"
        )


def test_state_is_the_one_geography_that_survives(cfg: dict, profiles: list[str]) -> None:
    """A profile that suppressed *everything* would pass every other test in this file.

    Safe Harbor removes geographic subdivisions **smaller than a state**, which means state
    itself is permitted -- and keeping it is most of the analytic value of the geography
    column. If this ever flips to suppress, the rulebook stopped being read and started
    being feared.
    """
    for profile in profiles:
        assert resolve_column_strategy(cfg, profile, "dim_patient", "StateAbbr")[0] == (
            "passthrough"
        )
        if _table_columns(cfg, profile, "clarity_patient"):
            assert resolve_column_strategy(cfg, profile, "clarity_patient", "STATE_C_NAME")[0] == (
                "passthrough"
            )


def test_both_sources_share_one_assigned_code_space_under_strict(cfg: dict) -> None:
    """Multi-source Safe Harbor lives or dies on this one property.

    Caboodle ``MRN`` and Clarity ``PAT_MRN_ID`` must resolve to ``surrogate`` with no
    namespace separating them, so the crosswalk minted over the union of both columns
    lands the same human on the same code. Give either one a namespace, or make one of
    them ``tokenize``, and the two schemas stop conforming -- quietly, with no error, and
    every cross-source count silently doubles.
    """
    strict = "safe_harbor_strict"
    caboodle = resolve_column_strategy(cfg, strict, "dim_patient", "MRN")
    clarity = resolve_column_strategy(cfg, strict, "clarity_patient", "PAT_MRN_ID")
    assert caboodle[0] == "surrogate"
    assert clarity[0] == "surrogate"
    assert caboodle[1].get("namespace") is None
    assert clarity[1].get("namespace") is None


def test_expert_determination_still_tokenizes_rather_than_assigning(cfg: dict) -> None:
    """The two methods must not quietly converge.

    Expert Determination keeps ``tokenize`` deliberately: an HMAC is recomputable forever,
    so there is no mapping to lose and no custody risk. Safe Harbor pays for its legality
    with custody of a crosswalk. That is a real trade-off, and if someone "simplifies" ED
    onto surrogates it disappears without anyone choosing to give it up.
    """
    strategy, params = resolve_column_strategy(cfg, "expert_determination", "dim_patient", "MRN")
    assert strategy == "tokenize"
    assert params.get("namespace") == "mrn"
