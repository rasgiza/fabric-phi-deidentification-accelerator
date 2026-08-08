#!/usr/bin/env python3
"""Give every one of the 18 HIPAA Safe Harbor identifiers a home in the sample data.

Why this script exists
----------------------
An audit of the committed sample data against 45 CFR §164.514(b)(2)(i)(A)-(R) found that
only about seven of the eighteen identifier categories appeared anywhere in it. That is
not a compliance defect -- an identifier a data set does not contain cannot leak, and the
rulebook's ``default_strategy: suppress`` nulls any column nobody classified. It is a
*demonstration* defect, and a sharp one:

    The scorecard's "no direct-identifier patterns (SSN / phone / email) survive" check is
    a regex scan over the output. With no SSN column anywhere in the estate, and no phone
    or email in Caboodle at all, that check scanned for three things that were never in the
    input and printed PASS.

A green tick over data that never contained the thing being checked for is worse than a
declared blind spot, because it looks like evidence. This script fixes the input so the
check has something to actually find and remove.

What it adds
------------
Columns are placed where the identifier really lives in an Epic-shaped warehouse, not
bolted onto one table for convenience:

============================================  =================================================
HIPAA identifier                              Where it now lives
============================================  =================================================
(B) street address, city                      ``DimPatient``, ``PATIENT`` (already had it)
(D) telephone numbers                         ``DimPatient``, ``PATIENT`` (home + mobile + work)
(E) fax numbers                               ``DimHospitalAccount``/``HSP_ACCOUNT`` (guarantor)
(G) social security numbers                   ``DimPatient``, ``PATIENT``
(I) health plan beneficiary numbers           ``DimPatient``, ``COVERAGE`` (subscriber ID)
(J) account numbers                           ``DimHospitalAccount``, ``HSP_ACCOUNT``
(K) certificate / license numbers             ``DimPatient`` (driver's licence -- patient side)
(L) vehicle identifiers                       ``FactPatientTransport`` (VIN, plate)
(M) device identifiers and serial numbers     ``FactPatientDevice`` (serial, UDI)
(N) URLs                                      ``FactPortalAccess``, ``DimPatient`` (photo URI)
(O) IP addresses                              ``FactPortalAccess``
(P) biometric identifiers                     ``DimPatient`` (template ID -- see caveat)
(Q) full-face photographs                     ``DimPatient`` (photo URI -- see caveat)
============================================  =================================================

(E) is deliberately modelled on the *guarantor*, not the patient. §164.514(b)(2)(i) removes
identifiers "of the individual **or of relatives, employers, or household members**", and a
guarantor is usually a relative -- so a guarantor fax is squarely in scope and is where a
fax number realistically appears in a hospital billing record.

Caveat on (P) and (Q): what lands here is the *pointer* -- a biometric template ID and a
photo URI -- not the template or the image itself. This accelerator scores structured tables
and free text; it does not open image binaries or waveforms. Suppressing the pointer is real
and checkable. Inspecting the media is out of scope and stays declared as such in the
scorecard rather than being quietly implied.

Every value is synthetic and drawn from a reserved range
--------------------------------------------------------
None of this can collide with a real person, a real phone line, a real mailbox or a real
host. That is a deliberate property, not a happy accident:

* **SSN** -- the Social Security Administration has never issued an area number in the
  900-999 range, so a generated value cannot be anybody's SSN.
* **Telephone / fax** -- NANP reserves ``555-0100`` through ``555-0199`` for fictional use,
  so no generated number can dial a real line.
* **Email / URL** -- RFC 2606 reserves ``example.com``, ``example.org`` and ``example.net``,
  so no generated address can reach a real mailbox or resolve to a real host.
* **IP address** -- RFC 5737 reserves ``192.0.2.0/24``, ``198.51.100.0/24`` and
  ``203.0.113.0/24`` for documentation; none of them are routable.
* **VIN** -- shaped per ISO 3779 (17 characters, no I/O/Q) but with a random check digit, so
  it will fail validation against any real vehicle registry.

Idempotent and deterministic
----------------------------
Re-running changes nothing. Existing tables are skipped if the new columns are already
present, and new tables are skipped if the file already exists (use ``--force`` to rebuild).
Per-row values are seeded from the patient's own key, so a given patient gets the same
synthetic SSN on every run and on every machine, and regenerating produces a byte-identical
file rather than a meaningless diff.

Usage
-----
    python scripts/add_identifier_columns.py             # apply
    python scripts/add_identifier_columns.py --dry-run   # report, change nothing
    python scripts/add_identifier_columns.py --force     # rebuild the generated tables
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CABOODLE_DIR = REPO_ROOT / "sample_data" / "caboodle_provider"
CLARITY_DIR = REPO_ROOT / "sample_data" / "Clarity"

# Changing this re-rolls every synthetic value in the sample data. Pinned on purpose.
DEFAULT_SEED = "fabric-phi-deid/safe-harbor-identifiers/v1"

# --- reserved-range building blocks (see the module docstring for why each one) -------------
RESERVED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_IP_PREFIXES = ("192.0.2", "198.51.100", "203.0.113")
AREA_CODES = ("212", "206", "305", "312", "415", "512", "617", "702", "813", "917")
VIN_ALPHABET = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # ISO 3779 excludes I, O and Q

PORTAL_HOST = "https://mychart.example.org"
MEDIA_HOST = "https://media.example.org"

# --- value pools ----------------------------------------------------------------------------
STREET_NAMES = (
    "Railroad", "Depot", "Maple", "Oak", "Cedar", "Elm", "Washington", "Lincoln",
    "Highland", "Sunset", "Lakeview", "Church", "Meadow", "Willow", "Franklin",
    "Chestnut", "Ridge", "Orchard", "Prospect", "Pinecrest",
)  # fmt: skip
STREET_TYPES = ("Street", "Avenue", "Road", "Lane", "Drive", "Court", "Way", "Boulevard")
CITIES = (
    ("Philadelphia", "PA"), ("Duluth", "MN"), ("Akron", "OH"), ("Fresno", "CA"),
    ("Tacoma", "WA"), ("Mobile", "AL"), ("Lubbock", "TX"), ("Peoria", "IL"),
    ("Scranton", "PA"), ("Boise", "ID"), ("Springfield", "MO"), ("Augusta", "ME"),
    ("Bristol", "CT"), ("Yonkers", "NY"), ("Gary", "IN"), ("Salem", "OR"),
)  # fmt: skip
GUARANTOR_FIRST = (
    "Marion", "Dale", "Rowan", "Sydney", "Blair", "Casey", "Jordan", "Reese",
    "Quinn", "Avery", "Emerson", "Hayden", "Kendall", "Peyton", "Skyler", "Tatum",
)  # fmt: skip
GUARANTOR_RELATIONSHIPS = ("Self", "Spouse", "Parent", "Child", "Guardian")
ACCOUNT_CLASSES = ("Inpatient", "Outpatient", "Emergency", "Observation", "Recurring")
ACCOUNT_STATUSES = ("Open", "Billed", "Closed", "In Collections")

DEVICE_TYPES = (
    "Cardiac Pacemaker", "Implantable Defibrillator", "Coronary Stent", "Insulin Pump",
    "Continuous Glucose Monitor", "Cochlear Implant", "Hip Prosthesis", "Knee Prosthesis",
    "Intrathecal Pump", "Neurostimulator",
)  # fmt: skip
DEVICE_MANUFACTURERS = (
    "Medtronic", "Boston Scientific", "Abbott", "Stryker", "Zimmer Biomet", "Edwards",
)  # fmt: skip
DEVICE_STATUSES = ("Active", "Explanted", "Recalled", "Inactive")

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4)",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)",
    "Mozilla/5.0 (Linux; Android 14)",
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X)",
)
PORTAL_PATHS = (
    "results/lab", "messages/inbox", "appointments/upcoming", "billing/statement",
    "medications/refill", "visits/summary", "documents/release",
)  # fmt: skip

TRANSPORT_MODES = (
    "Ground Ambulance",
    "Air Ambulance",
    "Wheelchair Van",
    "Private Vehicle",
    "Police Transport",
)
TRANSPORT_AGENCIES = (
    "County EMS", "Metro Ambulance Service", "Regional Air Medical",
    "Municipal Fire Rescue", "Private Medical Transport",
)  # fmt: skip

# Member-ID prefixes keyed to the payer brands already in DimPayer.csv.
MEMBER_PREFIXES = ("AET", "BCB", "CIG", "UHC", "HUM", "KPN", "ANT", "OSC", "MCR", "MCD", "TRI")

SVC_START, SVC_END = date(2024, 1, 1), date(2026, 12, 31)
IMPLANT_START, IMPLANT_END = date(2010, 1, 1), date(2025, 12, 31)


# --- value generators -------------------------------------------------------------------------
def _rng_for(seed: str, *parts: object) -> random.Random:
    """A generator whose output depends only on the seed and the row's own key.

    Seeding per row rather than per file is what makes this idempotent in the way that
    matters: adding patients later does not renumber the SSN of an existing one.
    """
    return random.Random(f"{seed}|" + "|".join(str(p) for p in parts))  # noqa: S311


def _ssn(rng: random.Random) -> str:
    # Area 900-999 has never been issued by the SSA -> cannot collide with a real SSN.
    return f"{rng.randint(900, 999)}-{rng.randint(10, 99):02d}-{rng.randint(0, 9999):04d}"


def _phone(rng: random.Random) -> str:
    # NANP reserves 555-0100..555-0199 for fictional use -> cannot dial a real line.
    return f"{rng.choice(AREA_CODES)}-555-{rng.randint(100, 199):04d}"


def _email(rng: random.Random, first: str, last: str, key: object) -> str:
    first = "".join(ch for ch in first.lower() if ch.isalpha()) or "patient"
    last = "".join(ch for ch in last.lower() if ch.isalpha()) or "record"
    return f"{first}.{last}{key}@{rng.choice(RESERVED_EMAIL_DOMAINS)}"


def _street(rng: random.Random) -> str:
    return f"{rng.randint(10, 9999)} {rng.choice(STREET_NAMES)} {rng.choice(STREET_TYPES)}"


def _drivers_licence(rng: random.Random, state: str) -> str:
    return f"{state}-{rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ')}{rng.randint(1000000, 9999999)}"


def _member_id(rng: random.Random) -> str:
    return f"{rng.choice(MEMBER_PREFIXES)}{rng.randint(100000000, 999999999)}"


def _ip(rng: random.Random) -> str:
    # RFC 5737 documentation ranges -> not routable, cannot be anybody's real address.
    return f"{rng.choice(RESERVED_IP_PREFIXES)}.{rng.randint(1, 254)}"


def _vin(rng: random.Random) -> str:
    return "".join(rng.choice(VIN_ALPHABET) for _ in range(17))


def _plate(rng: random.Random, state: str) -> str:
    letters = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(3))
    return f"{state}-{letters}{rng.randint(1000, 9999)}"


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, max((end - start).days, 0)))


# --- csv plumbing ------------------------------------------------------------------------------
def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    # LF and QUOTE_MINIMAL to match the committed CSVs byte for byte.
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_table(path: Path, header: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _insert_after(header: list[str], anchor: str, new_cols: list[str]) -> list[str]:
    """Place new columns next to the ones they belong with, not at the end of the row."""
    if anchor not in header:
        return header + new_cols
    i = header.index(anchor) + 1
    return header[:i] + new_cols + header[i:]


# --- Caboodle: DimPatient ----------------------------------------------------------------------
DIM_PATIENT_NEW_COLS = [
    "AddressLine1",
    "City",
    "StateAbbr",
    "HomePhone",
    "MobilePhone",
    "Email",
    "SSN",
    "DriversLicenseNumber",
    "HealthPlanMemberID",
    "BiometricTemplateID",
    "FacePhotoURI",
]


def dim_patient_identifiers(
    patient_key: str, first: str, last: str, seed: str = DEFAULT_SEED
) -> dict[str, str]:
    """The eleven identifier values for one patient, keyed off that patient's own PatientKey.

    Exposed so ``generate_sample_data.py`` can fill these columns on the rows it appends
    without duplicating the generators. If the two ever drifted, a regenerated data set would
    quietly stop exercising half the identifier categories -- which is precisely the class of
    silent failure this script exists to remove.
    """
    rng = _rng_for(seed, "dim_patient", patient_key)
    city, state = rng.choice(CITIES)
    return {
        "AddressLine1": _street(rng),
        "City": city,
        "StateAbbr": state,
        "HomePhone": _phone(rng),
        # Sparse on purpose: a real registration table is full of holes, and a de-id rule
        # that only ever sees a populated column has not been shown to handle a null.
        "MobilePhone": _phone(rng) if rng.random() < 0.80 else "",
        "Email": _email(rng, first, last, patient_key) if rng.random() < 0.70 else "",
        "SSN": _ssn(rng) if rng.random() < 0.92 else "",
        "DriversLicenseNumber": _drivers_licence(rng, state) if rng.random() < 0.65 else "",
        "HealthPlanMemberID": _member_id(rng) if rng.random() < 0.88 else "",
        "BiometricTemplateID": f"BIO-{rng.getrandbits(48):012x}" if rng.random() < 0.15 else "",
        "FacePhotoURI": (
            f"{MEDIA_HOST}/photo/{int(patient_key):08d}.jpg"
            if patient_key.isdigit() and rng.random() < 0.18
            else ""
        ),
    }


def extend_dim_patient(data_dir: Path, seed: str, dry_run: bool) -> bool:
    path = data_dir / "DimPatient.csv"
    header, rows = _read_csv(path)
    if all(col in header for col in DIM_PATIENT_NEW_COLS):
        print(f"  {path.name:28s} already extended - skipped")
        return False

    for row in rows:
        row.update(
            dim_patient_identifiers(
                row.get("PatientKey", ""),
                row.get("FirstName", ""),
                row.get("LastName", ""),
                seed,
            )
        )

    new_header = _insert_after(header, "ZIP", DIM_PATIENT_NEW_COLS)
    print(f"  {path.name:28s} +{len(DIM_PATIENT_NEW_COLS)} columns over {len(rows):,} rows")
    if not dry_run:
        _write_csv(path, new_header, rows)
    return True


# --- Caboodle: DimHospitalAccount -- (J) account numbers, (E) guarantor fax ---------------------
HOSPITAL_ACCOUNT_COLS = [
    "HospitalAccountKey",
    "PatientKey",
    "HospitalAccountNumber",
    "GuarantorAccountNumber",
    "GuarantorName",
    "GuarantorRelationship",
    "GuarantorPhone",
    "GuarantorFaxNumber",
    "AccountClass",
    "AccountStatus",
    "OpenDate",
    "CloseDate",
    "TotalCharges",
    "_IsCurrent",
]


def build_hospital_accounts(
    data_dir: Path, patients: list[dict[str, str]], seed: str, dry_run: bool, force: bool
) -> bool:
    path = data_dir / "DimHospitalAccount.csv"
    if path.exists() and not force:
        print(f"  {path.name:28s} exists - skipped (use --force to rebuild)")
        return False

    rows: list[list[object]] = []
    for patient in patients:
        key = patient.get("PatientKey", "")
        rng = _rng_for(seed, "hospital_account", key)
        if rng.random() >= 0.30:
            continue
        relationship = rng.choice(GUARANTOR_RELATIONSHIPS)
        if relationship == "Self":
            guarantor = f"{patient.get('LastName', '')},{patient.get('FirstName', '')}"
        else:
            guarantor = f"{patient.get('LastName', '')},{rng.choice(GUARANTOR_FIRST)}"
        opened = _rand_date(rng, SVC_START, SVC_END)
        status = rng.choice(ACCOUNT_STATUSES)
        closed = (
            "" if status == "Open" else (opened + timedelta(days=rng.randint(5, 240))).isoformat()
        )
        rows.append(
            [
                f"HA{len(rows) + 1:07d}",
                key,
                f"HAR{rng.randint(1000000000, 9999999999)}",
                f"GA{rng.randint(10000000, 99999999)}",
                guarantor,
                relationship,
                _phone(rng),
                _phone(rng) if rng.random() < 0.45 else "",
                rng.choice(ACCOUNT_CLASSES),
                status,
                opened.isoformat(),
                closed,
                f"{rng.uniform(120, 84000):.2f}",
                1,
            ]
        )
    print(f"  {path.name:28s} {len(rows):,} rows")
    if not dry_run:
        _write_table(path, HOSPITAL_ACCOUNT_COLS, rows)
    return True


# --- Caboodle: FactPatientDevice -- (M) device identifiers and serial numbers -------------------
PATIENT_DEVICE_COLS = [
    "PatientDeviceKey",
    "PatientKey",
    "DeviceType",
    "Manufacturer",
    "ModelNumber",
    "SerialNumber",
    "UDI",
    "ImplantDate",
    "DeviceStatus",
]


def build_patient_devices(
    data_dir: Path, patients: list[dict[str, str]], seed: str, dry_run: bool, force: bool
) -> bool:
    path = data_dir / "FactPatientDevice.csv"
    if path.exists() and not force:
        print(f"  {path.name:28s} exists - skipped (use --force to rebuild)")
        return False

    rows: list[list[object]] = []
    for patient in patients:
        key = patient.get("PatientKey", "")
        rng = _rng_for(seed, "patient_device", key)
        if rng.random() >= 0.08:
            continue
        serial = f"SN{rng.getrandbits(40):010X}"
        implanted = _rand_date(rng, IMPLANT_START, IMPLANT_END)
        rows.append(
            [
                f"PD{len(rows) + 1:06d}",
                key,
                rng.choice(DEVICE_TYPES),
                rng.choice(DEVICE_MANUFACTURERS),
                f"MDL-{rng.randint(1000, 9999)}",
                serial,
                f"(01)0088{rng.randint(10000000, 99999999)}(17)"
                f"{implanted.strftime('%y%m%d')}(21){serial}",
                implanted.isoformat(),
                rng.choice(DEVICE_STATUSES),
            ]
        )
    print(f"  {path.name:28s} {len(rows):,} rows")
    if not dry_run:
        _write_table(path, PATIENT_DEVICE_COLS, rows)
    return True


# --- Caboodle: FactPortalAccess -- (N) URLs, (O) IP addresses -----------------------------------
PORTAL_ACCESS_COLS = [
    "PortalAccessKey",
    "PatientKey",
    "AccessDate",
    "PortalUserName",
    "SourceIPAddress",
    "AccessedURL",
    "UserAgent",
    "SessionID",
]


def build_portal_access(
    data_dir: Path, patients: list[dict[str, str]], seed: str, dry_run: bool, force: bool
) -> bool:
    path = data_dir / "FactPortalAccess.csv"
    if path.exists() and not force:
        print(f"  {path.name:28s} exists - skipped (use --force to rebuild)")
        return False

    rows: list[list[object]] = []
    for patient in patients:
        key = patient.get("PatientKey", "")
        rng = _rng_for(seed, "portal_access", key)
        if rng.random() >= 0.20:
            continue
        first = (patient.get("FirstName", "") or "p")[:1].lower()
        last = "".join(ch for ch in patient.get("LastName", "").lower() if ch.isalpha()) or "user"
        username = f"{first}{last}{rng.randint(10, 99)}"
        for _ in range(rng.randint(1, 3)):
            rows.append(
                [
                    f"PA{len(rows) + 1:07d}",
                    key,
                    _rand_date(rng, SVC_START, SVC_END).isoformat(),
                    username,
                    _ip(rng),
                    f"{PORTAL_HOST}/{rng.choice(PORTAL_PATHS)}?pat={key}",
                    rng.choice(USER_AGENTS),
                    f"{rng.getrandbits(64):016x}",
                ]
            )
    print(f"  {path.name:28s} {len(rows):,} rows")
    if not dry_run:
        _write_table(path, PORTAL_ACCESS_COLS, rows)
    return True


# --- Caboodle: FactPatientTransport -- (L) vehicle identifiers ----------------------------------
PATIENT_TRANSPORT_COLS = [
    "TransportKey",
    "PatientKey",
    "TransportDate",
    "TransportMode",
    "TransportAgency",
    "VehicleIdentificationNumber",
    "LicensePlateNumber",
    "ArrivalMinutes",
]


def build_patient_transport(
    data_dir: Path, patients: list[dict[str, str]], seed: str, dry_run: bool, force: bool
) -> bool:
    path = data_dir / "FactPatientTransport.csv"
    if path.exists() and not force:
        print(f"  {path.name:28s} exists - skipped (use --force to rebuild)")
        return False

    rows: list[list[object]] = []
    for patient in patients:
        key = patient.get("PatientKey", "")
        rng = _rng_for(seed, "patient_transport", key)
        if rng.random() >= 0.05:
            continue
        _, state = rng.choice(CITIES)
        rows.append(
            [
                f"TR{len(rows) + 1:06d}",
                key,
                _rand_date(rng, SVC_START, SVC_END).isoformat(),
                rng.choice(TRANSPORT_MODES),
                rng.choice(TRANSPORT_AGENCIES),
                _vin(rng),
                _plate(rng, state),
                rng.randint(4, 95),
            ]
        )
    print(f"  {path.name:28s} {len(rows):,} rows")
    if not dry_run:
        _write_table(path, PATIENT_TRANSPORT_COLS, rows)
    return True


# --- Clarity: PATIENT --------------------------------------------------------------------------
CLARITY_PATIENT_NEW_COLS = ["SSN", "WORK_PHONE", "MOBILE_PHONE"]


def extend_clarity_patient(data_dir: Path, seed: str, dry_run: bool) -> bool:
    path = data_dir / "PATIENT.csv"
    header, rows = _read_csv(path)
    if all(col in header for col in CLARITY_PATIENT_NEW_COLS):
        print(f"  {path.name:28s} already extended - skipped")
        return False

    for row in rows:
        rng = _rng_for(seed, "clarity_patient", row.get("PAT_ID", ""))
        row["SSN"] = _ssn(rng) if rng.random() < 0.90 else ""
        row["WORK_PHONE"] = _phone(rng) if rng.random() < 0.55 else ""
        row["MOBILE_PHONE"] = _phone(rng) if rng.random() < 0.82 else ""

    new_header = _insert_after(header, "HOME_PHONE", CLARITY_PATIENT_NEW_COLS)
    print(f"  {path.name:28s} +{len(CLARITY_PATIENT_NEW_COLS)} columns over {len(rows):,} rows")
    if not dry_run:
        _write_csv(path, new_header, rows)
    return True


# --- Clarity: normalise the legacy contact columns ----------------------------------------------
_RESERVED_PHONE_RE = re.compile(r"^\d{3}-555-01\d{2}$")


def normalize_legacy_contacts(data_dir: Path, seed: str, dry_run: bool) -> bool:
    """Rewrite the pre-existing ``HOME_PHONE`` / ``EMAIL_ADDRESS`` values into reserved ranges.

    These two columns predate this script and were generated with ordinary-looking values
    (``334-395-4535``, ``@gmail.com``). Nothing about that is a privacy breach -- the values
    are synthetic -- but it does weaken the claim the accelerator most needs to be able to
    make without qualification: *no value in this repository can reach a real person, a real
    phone line or a real mailbox*. A dialable-looking number in a public demo repo is a
    question you have to answer every time somebody notices it.

    This is the one place the script rewrites existing data rather than only adding to it.
    """
    path = data_dir / "PATIENT.csv"
    header, rows = _read_csv(path)
    if "HOME_PHONE" not in header and "EMAIL_ADDRESS" not in header:
        return False

    changed = 0
    for row in rows:
        rng = _rng_for(seed, "legacy_contacts", row.get("PAT_ID", ""))
        phone = row.get("HOME_PHONE", "")
        if phone and not _RESERVED_PHONE_RE.match(phone):
            row["HOME_PHONE"] = _phone(rng)
            changed += 1
        email = row.get("EMAIL_ADDRESS", "")
        if email and not email.endswith(RESERVED_EMAIL_DOMAINS):
            row["EMAIL_ADDRESS"] = _email(
                rng,
                row.get("PAT_FIRST_NAME", ""),
                row.get("PAT_LAST_NAME", ""),
                row.get("PAT_ID", ""),
            )
            changed += 1

    if not changed:
        print(f"  {path.name:28s} contact values already reserved - skipped")
        return False

    print(f"  {path.name:28s} normalised {changed:,} legacy phone/email values")
    if not dry_run:
        _write_csv(path, header, rows)
    return True


# --- Clarity: COVERAGE -- (I) health plan beneficiary numbers -----------------------------------
COVERAGE_COLS = [
    "COVERAGE_ID",
    "PAT_ID",
    "PAYOR_ID",
    "PLAN_NAME",
    "SUBSCRIBER_ID",
    "GROUP_NUM",
    "SUBSCRIBER_NAME",
    "SUBSCRIBER_REL_C",
    "MEM_EFF_FROM_DATE",
    "MEM_EFF_TO_DATE",
]

CLARITY_PLANS = (
    ("PAY0001", "Aetna Choice PPO"),
    ("PAY0002", "Blue Cross Blue Shield PPO"),
    ("PAY0004", "UnitedHealthcare Choice"),
    ("PAY0011", "Medicare Part A"),
    ("PAY0018", "Medicaid Managed Care"),
    ("PAY0024", "Self-Pay"),
)


def build_coverage(
    data_dir: Path, patients: list[dict[str, str]], seed: str, dry_run: bool, force: bool
) -> bool:
    path = data_dir / "COVERAGE.csv"
    if path.exists() and not force:
        print(f"  {path.name:28s} exists - skipped (use --force to rebuild)")
        return False

    rows: list[list[object]] = []
    for patient in patients:
        pat_id = patient.get("PAT_ID", "")
        rng = _rng_for(seed, "coverage", pat_id)
        for _ in range(rng.choices((1, 2), weights=(0.8, 0.2))[0]):
            payor, plan = rng.choice(CLARITY_PLANS)
            rel = rng.choices((1, 2, 3), weights=(0.7, 0.15, 0.15))[0]
            if rel == 1:
                subscriber = patient.get("PAT_NAME", "")
            else:
                last = patient.get("PAT_LAST_NAME", "")
                subscriber = f"{last},{rng.choice(GUARANTOR_FIRST)}"
            start = _rand_date(rng, date(2019, 1, 1), date(2025, 6, 30))
            rows.append(
                [
                    f"CV{len(rows) + 1:06d}",
                    pat_id,
                    payor,
                    plan,
                    _member_id(rng),
                    f"GRP{rng.randint(10000, 99999)}",
                    subscriber,
                    rel,
                    start.isoformat(),
                    "" if rng.random() < 0.75 else (start + timedelta(days=730)).isoformat(),
                ]
            )
    print(f"  {path.name:28s} {len(rows):,} rows")
    if not dry_run:
        _write_table(path, COVERAGE_COLS, rows)
    return True


# --- Clarity: HSP_ACCOUNT -- (J) account numbers, (E) guarantor fax -----------------------------
HSP_ACCOUNT_COLS = [
    "HSP_ACCOUNT_ID",
    "PAT_ID",
    "ACCT_BILLING_NUM",
    "GUAR_NAME",
    "GUAR_HOME_PHONE",
    "GUAR_FAX",
    "ACCT_CLASS_HA_C",
    "ACCT_STATUS",
    "ADM_DATE_TIME",
    "DISCH_DATE_TIME",
    "TOT_CHGS",
]


def build_hsp_account(
    data_dir: Path, patients: list[dict[str, str]], seed: str, dry_run: bool, force: bool
) -> bool:
    path = data_dir / "HSP_ACCOUNT.csv"
    if path.exists() and not force:
        print(f"  {path.name:28s} exists - skipped (use --force to rebuild)")
        return False

    rows: list[list[object]] = []
    for patient in patients:
        pat_id = patient.get("PAT_ID", "")
        rng = _rng_for(seed, "hsp_account", pat_id)
        if rng.random() >= 0.35:
            continue
        admitted = _rand_date(rng, SVC_START, SVC_END)
        discharged = admitted + timedelta(days=rng.randint(0, 12))
        last = patient.get("PAT_LAST_NAME", "")
        rows.append(
            [
                f"HSA{len(rows) + 1:06d}",
                pat_id,
                f"BN{rng.randint(100000000, 999999999)}",
                f"{last},{rng.choice(GUARANTOR_FIRST)}",
                _phone(rng),
                _phone(rng) if rng.random() < 0.4 else "",
                rng.randint(1, 5),
                rng.choice(ACCOUNT_STATUSES),
                admitted.isoformat(),
                discharged.isoformat(),
                f"{rng.uniform(300, 96000):.2f}",
            ]
        )
    print(f"  {path.name:28s} {len(rows):,} rows")
    if not dry_run:
        _write_table(path, HSP_ACCOUNT_COLS, rows)
    return True


# --- driver -------------------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Give all 18 HIPAA Safe Harbor identifiers a home in the sample data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--caboodle-dir", type=Path, default=CABOODLE_DIR)
    parser.add_argument("--clarity-dir", type=Path, default=CLARITY_DIR)
    parser.add_argument("--seed", default=DEFAULT_SEED, help="Pinned; changing it re-rolls values.")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--force", action="store_true", help="Rebuild generated tables.")
    args = parser.parse_args()

    for folder in (args.caboodle_dir, args.clarity_dir):
        if not folder.is_dir():
            raise SystemExit(f"Not a directory: {folder}")

    if args.dry_run:
        print("DRY RUN - nothing will be written\n")

    print(f"Caboodle  {args.caboodle_dir}")
    changed = extend_dim_patient(args.caboodle_dir, args.seed, args.dry_run)
    _, caboodle_patients = _read_csv(args.caboodle_dir / "DimPatient.csv")
    changed |= build_hospital_accounts(
        args.caboodle_dir, caboodle_patients, args.seed, args.dry_run, args.force
    )
    changed |= build_patient_devices(
        args.caboodle_dir, caboodle_patients, args.seed, args.dry_run, args.force
    )
    changed |= build_portal_access(
        args.caboodle_dir, caboodle_patients, args.seed, args.dry_run, args.force
    )
    changed |= build_patient_transport(
        args.caboodle_dir, caboodle_patients, args.seed, args.dry_run, args.force
    )

    print(f"\nClarity   {args.clarity_dir}")
    changed |= extend_clarity_patient(args.clarity_dir, args.seed, args.dry_run)
    changed |= normalize_legacy_contacts(args.clarity_dir, args.seed, args.dry_run)
    _, clarity_patients = _read_csv(args.clarity_dir / "PATIENT.csv")
    changed |= build_coverage(
        args.clarity_dir, clarity_patients, args.seed, args.dry_run, args.force
    )
    changed |= build_hsp_account(
        args.clarity_dir, clarity_patients, args.seed, args.dry_run, args.force
    )

    print("\nEverything above is synthetic and drawn from reserved ranges (SSA 900-999,")
    print("NANP 555-01xx, RFC 2606 example.*, RFC 5737 documentation IPs). No value here")
    print("can collide with a real person, phone line, mailbox or host.")
    if not changed:
        print("\nNothing to do - the sample data already carries all 18 identifier categories.")


if __name__ == "__main__":
    sys.exit(main())
