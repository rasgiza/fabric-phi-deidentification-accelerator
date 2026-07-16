#!/usr/bin/env python3
"""Extend the synthetic Caboodle sample dataset with more rows.

The accelerator ships a synthetic Caboodle provider dataset (generated with
Tonic Fabricate) under ``sample_data/caboodle_provider/``. That committed set is
enough to run the whole Bronze -> Silver -> Gold pipeline end to end. When you
need a bigger volume (load testing, more realistic k-anonymity behaviour, demo
variety) this script appends additional **synthetic** rows to the fact and
patient tables while preserving referential integrity to the existing keys.

Design choices
--------------
* Standard library only (``csv``, ``random``, ``argparse``, ``datetime``) so it
  runs anywhere with no ``pip install``.
* The curated dimension/provider tables (departments, diagnoses, procedures,
  payers, facilities, providers, credentials, specialties, bridge) are treated
  as fixed reference data and are **not** modified. New patients and fact rows
  reference the existing keys, so every foreign key stays valid.
* Categorical value pools (Gender, Race, Ethnicity, ClaimStatus, ...) are
  sampled from the existing CSVs at run time, so generated rows match the shape
  of the seed data without hard-coding domain values here.
* Everything produced is synthetic. There is no real PHI anywhere in this repo
  or in anything this script emits.

Examples
--------
Append 20k claims, 10k encounters, 5k risk scores and 2k patients (seeded)::

    python scripts/generate_sample_data.py \\
        --add-patients 2000 --add-claims 20000 \\
        --add-encounters 10000 --add-risk-scores 5000 --seed 42

Only grow the claim fact by 100k rows (referencing existing patients)::

    python scripts/generate_sample_data.py --add-claims 100000

Point at a copy of the data instead of the committed sample::

    python scripts/generate_sample_data.py --data-dir /tmp/mydata --add-claims 1000
"""

from __future__ import annotations

import argparse
import csv
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "sample_data" / "caboodle_provider"

# Column order must match the CSVs exactly — Bronze ingest reads by position/name.
PATIENT_COLS = [
    "PatientKey",
    "PatientDurableKey",
    "MRN",
    "FirstName",
    "LastName",
    "PatientName",
    "DateOfBirth",
    "Gender",
    "Race",
    "Ethnicity",
    "ZIP",
    "PCPProviderKey",
    "_IsCurrent",
    "EffectiveDate",
    "ExpirationDate",
]
CLAIM_COLS = [
    "ClaimKey",
    "PatientKey",
    "BillingProviderKey",
    "RenderingProviderKey",
    "PayerKey",
    "ProcedureKey",
    "DiagnosisKey",
    "ServiceDate",
    "BilledAmount",
    "AllowedAmount",
    "PaidAmount",
    "ClaimStatus",
]
ENCOUNTER_COLS = [
    "EncounterKey",
    "PatientKey",
    "AttendingProviderKey",
    "ReferringProviderKey",
    "DepartmentKey",
    "LocationKey",
    "DiagnosisKey",
    "EncounterDate",
    "EncounterType",
    "LengthOfStay",
]
RISK_COLS = ["RiskScoreKey", "PatientKey", "ProviderKey", "RiskModel", "RiskScore", "ScoreDate"]

# Small synthetic name pools for new patients (deliberately generic, non-real).
FIRST_NAMES = [
    "Harper",
    "Liam",
    "Olivia",
    "Noah",
    "Emma",
    "Aiden",
    "Ava",
    "Mason",
    "Sophia",
    "Lucas",
    "Isabella",
    "Ethan",
    "Mia",
    "Logan",
    "Amelia",
    "Jackson",
    "Charlotte",
    "Elijah",
    "Evelyn",
    "James",
    "Abigail",
    "Benjamin",
    "Harper",
    "Daniel",
    "Ella",
    "Henry",
    "Scarlett",
    "Sebastian",
    "Grace",
    "Jack",
    "Chloe",
    "Owen",
    "Victoria",
    "Wyatt",
    "Riley",
    "Julian",
    "Aria",
    "Levi",
    "Lily",
    "Isaac",
    "Nora",
    "Gabriel",
]
LAST_NAMES = [
    "Rogers",
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
    "Ramirez",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Torres",
    "Nguyen",
    "Hill",
]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        header = reader.fieldnames or []
    return list(header), rows


def _int_keys(rows: list[dict[str, str]], col: str) -> list[int]:
    out: list[int] = []
    for r in rows:
        val = r.get(col, "").strip()
        if val:
            out.append(int(val))
    return out


def _pool(rows: list[dict[str, str]], col: str) -> list[str]:
    """Weighted value pool: return the observed values (with repeats) for `col`."""
    vals = [r[col] for r in rows if r.get(col, "").strip() != ""]
    if not vals:
        return [""]
    # Collapse to the observed distribution but cap repeats to keep it light.
    counts = Counter(vals)
    pool: list[str] = []
    for value, n in counts.items():
        pool.extend([value] * min(n, 50))
    return pool


def _append_rows(path: Path, cols: list[str], new_rows: list[list[str]]) -> None:
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        for row in new_rows:
            writer.writerow(row)
    print(f"  {path.name:32s} +{len(new_rows):,} rows")


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    span = (end - start).days
    return start + timedelta(days=rng.randint(0, max(span, 0)))


def generate(  # noqa: PLR0913 - explicit knobs are clearer than a config object here
    data_dir: Path,
    *,
    add_patients: int,
    add_claims: int,
    add_encounters: int,
    add_risk_scores: int,
    seed: int | None,
) -> None:
    rng = random.Random(seed)  # noqa: S311 - synthetic sample data, not a cryptographic use

    # --- load reference keys / pools from the existing data ---
    _, patients = _read_csv(data_dir / "DimPatient.csv")
    _, providers = _read_csv(data_dir / "DimProvider.csv")
    _, payers = _read_csv(data_dir / "DimPayer.csv")
    _, procedures = _read_csv(data_dir / "DimProcedure.csv")
    _, diagnoses = _read_csv(data_dir / "DimDiagnosis.csv")
    _, departments = _read_csv(data_dir / "DimDepartment.csv")
    _, facilities = _read_csv(data_dir / "DimFacility.csv")

    provider_keys = _int_keys(providers, "ProviderKey")
    payer_keys = _int_keys(payers, "PayerKey")
    procedure_keys = _int_keys(procedures, "ProcedureKey")
    diagnosis_keys = _int_keys(diagnoses, "DiagnosisKey")
    department_keys = _int_keys(departments, "DepartmentKey")
    facility_keys = _int_keys(facilities, "FacilityKey")

    gender_pool = _pool(patients, "Gender")
    race_pool = _pool(patients, "Race")
    ethnicity_pool = _pool(patients, "Ethnicity")
    zip_pool = _pool(patients, "ZIP")

    # --- patients (must run first so new fact rows can reference them) ---
    patient_keys = _int_keys(patients, "PatientKey")
    max_patient_key = max(patient_keys) if patient_keys else 0
    if add_patients > 0:
        new_patient_rows: list[list[str]] = []
        for i in range(1, add_patients + 1):
            pk = max_patient_key + i
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            dob = _rand_date(rng, date(1930, 1, 1), date(2015, 12, 31))
            new_patient_rows.append(
                [
                    pk,
                    500000 + pk,
                    f"MRN{pk:08d}",
                    first,
                    last,
                    f"{first} {last}",
                    dob.isoformat(),
                    rng.choice(gender_pool),
                    rng.choice(race_pool),
                    rng.choice(ethnicity_pool),
                    rng.choice(zip_pool),
                    rng.choice(provider_keys),
                    1,
                    "2015-01-01",
                    "9999-12-31",
                ]
            )
        _append_rows(data_dir / "DimPatient.csv", PATIENT_COLS, new_patient_rows)
        patient_keys.extend(range(max_patient_key + 1, max_patient_key + add_patients + 1))

    if not patient_keys:
        raise SystemExit("No patients available to reference; add patients first.")

    svc_start, svc_end = date(2024, 1, 1), date(2026, 12, 31)

    # --- claims ---
    if add_claims > 0:
        _, claims = _read_csv(data_dir / "FactClaim.csv")
        status_pool = _pool(claims, "ClaimStatus")
        max_claim_key = max(_int_keys(claims, "ClaimKey") or [0])
        del claims  # free the 500k-row list before building new rows
        new_claim_rows: list[list[str]] = []
        for i in range(1, add_claims + 1):
            billed = round(rng.uniform(75, 5200), 2)
            allowed = round(billed * rng.uniform(0.45, 0.9), 2)
            status = rng.choice(status_pool)
            paid = (
                allowed if status.lower() == "paid" else round(allowed * rng.uniform(0.0, 0.8), 2)
            )
            new_claim_rows.append(
                [
                    max_claim_key + i,
                    rng.choice(patient_keys),
                    rng.choice(provider_keys),
                    rng.choice(provider_keys),
                    rng.choice(payer_keys),
                    rng.choice(procedure_keys),
                    rng.choice(diagnosis_keys),
                    _rand_date(rng, svc_start, svc_end).isoformat(),
                    f"{billed:.2f}",
                    f"{allowed:.2f}",
                    f"{paid:.2f}",
                    status,
                ]
            )
        _append_rows(data_dir / "FactClaim.csv", CLAIM_COLS, new_claim_rows)

    # --- encounters ---
    if add_encounters > 0:
        _, encounters = _read_csv(data_dir / "FactEncounter.csv")
        type_pool = _pool(encounters, "EncounterType")
        max_enc_key = max(_int_keys(encounters, "EncounterKey") or [0])
        del encounters
        new_enc_rows: list[list[str]] = []
        for i in range(1, add_encounters + 1):
            enc_type = rng.choice(type_pool)
            los = rng.randint(1, 14) if enc_type.lower() == "inpatient" else 0
            referring = rng.choice(provider_keys) if rng.random() > 0.4 else ""
            new_enc_rows.append(
                [
                    max_enc_key + i,
                    rng.choice(patient_keys),
                    rng.choice(provider_keys),
                    referring,
                    rng.choice(department_keys),
                    rng.choice(facility_keys),
                    rng.choice(diagnosis_keys),
                    _rand_date(rng, svc_start, svc_end).isoformat(),
                    enc_type,
                    los,
                ]
            )
        _append_rows(data_dir / "FactEncounter.csv", ENCOUNTER_COLS, new_enc_rows)

    # --- risk scores ---
    if add_risk_scores > 0:
        _, risks = _read_csv(data_dir / "FactRiskScore.csv")
        model_pool = _pool(risks, "RiskModel")
        max_risk_key = max(_int_keys(risks, "RiskScoreKey") or [0])
        del risks
        new_risk_rows: list[list[str]] = []
        for i in range(1, add_risk_scores + 1):
            new_risk_rows.append(
                [
                    max_risk_key + i,
                    rng.choice(patient_keys),
                    rng.choice(provider_keys),
                    rng.choice(model_pool),
                    f"{rng.uniform(0.1, 2.5):.3f}",
                    _rand_date(rng, svc_start, svc_end).isoformat(),
                ]
            )
        _append_rows(data_dir / "FactRiskScore.csv", RISK_COLS, new_risk_rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append synthetic rows to the Caboodle sample dataset (FK-safe).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Folder containing the 13 Caboodle CSVs (default: sample_data/caboodle_provider).",
    )
    parser.add_argument("--add-patients", type=int, default=0, help="New patients to append.")
    parser.add_argument("--add-claims", type=int, default=0, help="New claim rows to append.")
    parser.add_argument(
        "--add-encounters", type=int, default=0, help="New encounter rows to append."
    )
    parser.add_argument(
        "--add-risk-scores", type=int, default=0, help="New risk-score rows to append."
    )
    parser.add_argument("--seed", type=int, default=None, help="Seed for reproducible output.")
    args = parser.parse_args()

    if not args.data_dir.exists():
        raise SystemExit(f"Data dir not found: {args.data_dir}")
    if (args.add_patients | args.add_claims | args.add_encounters | args.add_risk_scores) == 0:
        parser.error(
            "Nothing to do — pass at least one of --add-patients/--add-claims/"
            "--add-encounters/--add-risk-scores."
        )

    print(f"Extending dataset in {args.data_dir}")
    generate(
        args.data_dir,
        add_patients=args.add_patients,
        add_claims=args.add_claims,
        add_encounters=args.add_encounters,
        add_risk_scores=args.add_risk_scores,
        seed=args.seed,
    )
    print("Done. All new rows reference existing dimension/provider keys.")


if __name__ == "__main__":
    main()
