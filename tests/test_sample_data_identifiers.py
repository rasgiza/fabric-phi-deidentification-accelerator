"""The sample data must exercise every HIPAA identifier, and must never look real.

Two independent guarantees are asserted here, both about the committed CSVs in
``sample_data/`` rather than about the code that reads them.

**1. Coverage.** Every Safe Harbor identifier category in §164.514(b)(2)(i) that this
accelerator claims to handle has at least one column carrying it. This exists because of a
specific finding: the scorecard's "no SSN / phone / email survives" check was passing over an
estate that had never contained an SSN column at all. A control that scans for something the
input never held is not evidence of anything -- it is a green tick with nothing behind it.
These tests fail loudly if a future change removes the columns that give that check teeth.

**2. Reserved ranges.** No phone number, email address or IP address anywhere in the sample
data can reach a real line, mailbox or host. That claim is made in the README, in the module
docstrings and out loud in demos, so it should be enforced rather than asserted:

* NANP reserves ``555-0100``-``555-0199`` for fiction.
* RFC 2606 reserves ``example.com`` / ``.org`` / ``.net``.
* RFC 5737 reserves ``192.0.2.0/24``, ``198.51.100.0/24``, ``203.0.113.0/24``.
* The SSA has never issued an SSN area number in ``900``-``999``.

Both suites are skipped rather than failed when ``sample_data/`` is absent, so a checkout
without the sample estate still runs green.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CABOODLE_DIR = REPO_ROOT / "sample_data" / "caboodle_provider"
CLARITY_DIR = REPO_ROOT / "sample_data" / "Clarity"

pytestmark = pytest.mark.skipif(
    not CABOODLE_DIR.is_dir() or not CLARITY_DIR.is_dir(),
    reason="sample_data/ not present in this checkout",
)

# Each HIPAA identifier this accelerator claims to handle, and one (file, column) that must
# carry it. Categories (P) and (Q) point at an identifier *pointer*, not the media itself --
# the scorecard reports those as NOT_EVALUATED and this table does not pretend otherwise.
IDENTIFIER_HOMES: dict[str, tuple[str, str]] = {
    "A names": ("caboodle_provider/DimPatient.csv", "PatientName"),
    "B street address": ("caboodle_provider/DimPatient.csv", "AddressLine1"),
    "B city": ("caboodle_provider/DimPatient.csv", "City"),
    "B zip": ("caboodle_provider/DimPatient.csv", "ZIP"),
    "C dates": ("caboodle_provider/DimPatient.csv", "DateOfBirth"),
    "D telephone": ("caboodle_provider/DimPatient.csv", "HomePhone"),
    "E fax": ("caboodle_provider/DimHospitalAccount.csv", "GuarantorFaxNumber"),
    "F email": ("caboodle_provider/DimPatient.csv", "Email"),
    "G ssn": ("caboodle_provider/DimPatient.csv", "SSN"),
    "H mrn": ("caboodle_provider/DimPatient.csv", "MRN"),
    "I health plan id": ("caboodle_provider/DimPatient.csv", "HealthPlanMemberID"),
    "J account number": ("caboodle_provider/DimHospitalAccount.csv", "HospitalAccountNumber"),
    "K licence number": ("caboodle_provider/DimPatient.csv", "DriversLicenseNumber"),
    "L vehicle id": ("caboodle_provider/FactPatientTransport.csv", "VehicleIdentificationNumber"),
    "L licence plate": ("caboodle_provider/FactPatientTransport.csv", "LicensePlateNumber"),
    "M device serial": ("caboodle_provider/FactPatientDevice.csv", "SerialNumber"),
    "M device udi": ("caboodle_provider/FactPatientDevice.csv", "UDI"),
    "N url": ("caboodle_provider/FactPortalAccess.csv", "AccessedURL"),
    "O ip address": ("caboodle_provider/FactPortalAccess.csv", "SourceIPAddress"),
    "P biometric pointer": ("caboodle_provider/DimPatient.csv", "BiometricTemplateID"),
    "Q photo pointer": ("caboodle_provider/DimPatient.csv", "FacePhotoURI"),
    "R other unique code": ("caboodle_provider/DimPatient.csv", "PatientKey"),
    # The second source has to carry them too, or a multi-source claim rests on one system.
    "clarity ssn": ("Clarity/PATIENT.csv", "SSN"),
    "clarity telephone": ("Clarity/PATIENT.csv", "HOME_PHONE"),
    "clarity email": ("Clarity/PATIENT.csv", "EMAIL_ADDRESS"),
    "clarity health plan id": ("Clarity/COVERAGE.csv", "SUBSCRIBER_ID"),
    "clarity account number": ("Clarity/HSP_ACCOUNT.csv", "ACCT_BILLING_NUM"),
    "clarity fax": ("Clarity/HSP_ACCOUNT.csv", "GUAR_FAX"),
}

# Deliberately loose so a value that merely *looks* dialable/routable is caught too.
PHONE_RE = re.compile(r"\b\d{3}[-.]\d{3}[-.]\d{4}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SSN_RE = re.compile(r"\b(\d{3})-\d{2}-\d{4}\b")

RESERVED_PHONE_RE = re.compile(r"^\d{3}-555-01\d{2}$")
RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_IP_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")


def _sample_csvs() -> list[Path]:
    return sorted(CABOODLE_DIR.glob("*.csv")) + sorted(CLARITY_DIR.glob("*.csv"))


@pytest.fixture(scope="module")
def offenders() -> dict[str, list[str]]:
    """Scan the whole sample estate once and bucket anything outside a reserved range.

    Four separate passes over ~60 MB of CSV cost about thirty seconds; one pass costs a
    quarter of that. A guard rail nobody wants to wait for is a guard rail that eventually
    gets skipped, so this is worth the small amount of structure.
    """
    found: dict[str, list[str]] = {"phone": [], "email": [], "ip": [], "ssn": []}
    for path in _sample_csvs():
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                for column, value in row.items():
                    if not value:
                        continue
                    where = f"{path.name}.{column}"
                    for match in PHONE_RE.findall(value):
                        if not RESERVED_PHONE_RE.match(match):
                            found["phone"].append(f"{where}: {match}")
                    if "@" in value:
                        for match in EMAIL_RE.findall(value):
                            if not match.endswith(RESERVED_EMAIL_DOMAINS):
                                found["email"].append(f"{where}: {match}")
                    for match in IPV4_RE.findall(value):
                        if not match.startswith(RESERVED_IP_PREFIXES):
                            found["ip"].append(f"{where}: {match}")
                    for area in SSN_RE.findall(value):
                        if int(area) < 900:
                            found["ssn"].append(f"{where}: {area}-xx-xxxx")
    return found


@pytest.mark.parametrize(("identifier", "home"), sorted(IDENTIFIER_HOMES.items()))
def test_identifier_has_a_populated_home(identifier: str, home: tuple[str, str]) -> None:
    """Existing *and* non-empty. A column of blanks gives the scorecard nothing to find."""
    relative, column = home
    path = REPO_ROOT / "sample_data" / relative
    assert path.is_file(), f"{identifier}: {relative} is missing"

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert column in (reader.fieldnames or []), f"{identifier}: {relative} has no {column}"
        assert any(row.get(column) for row in reader), (
            f"{identifier}: {relative}.{column} exists but is empty in every row, "
            "so any check that scans for it would pass vacuously"
        )


def test_no_dialable_phone_numbers(offenders: dict[str, list[str]]) -> None:
    assert not offenders["phone"], f"phone numbers outside NANP 555-01xx: {offenders['phone'][:5]}"


def test_no_routable_email_addresses(offenders: dict[str, list[str]]) -> None:
    assert not offenders["email"], (
        f"emails outside the RFC 2606 example.* domains: {offenders['email'][:5]}"
    )


def test_no_routable_ip_addresses(offenders: dict[str, list[str]]) -> None:
    assert not offenders["ip"], (
        f"IPs outside the RFC 5737 documentation ranges: {offenders['ip'][:5]}"
    )


def test_no_issuable_social_security_numbers(offenders: dict[str, list[str]]) -> None:
    """Area 900-999 has never been issued, so no generated SSN can be anybody's."""
    assert not offenders["ssn"], f"SSNs in the SSA-issuable range: {offenders['ssn'][:5]}"
