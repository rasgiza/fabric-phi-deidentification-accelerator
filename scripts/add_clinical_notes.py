#!/usr/bin/env python3
"""Backfill a synthetic free-text clinical note column onto FactEncounter.

Why this exists
---------------
HIPAA identifiers hide *inside* narrative text (notes, comments, reason-for-visit), not just
in dedicated columns. The structured rulebook in ``config/deid_rules.yaml`` classifies whole
columns; free text needs detection + redaction (``fabric_phi_deid.ner_text``). Without a
free-text column in the sample data there is nothing to exercise that path, so this script
adds one: ``ReasonForVisitNote`` on ``FactEncounter.csv``.

Every note is **synthetic**. Names and MRNs are pulled from the synthetic ``DimPatient.csv``;
phone numbers use the reserved ``555-01xx`` range and emails use ``example.com``, both of
which are non-routable by convention. There is no real PHI in this repo.

The notes deliberately embed identifiers the de-id engine must find:

===========================  ==========================================
Identifier planted           Detected as
===========================  ==========================================
Patient name                 ``PERSON``       (Presidio only)
MRN                          ``MEDICAL_RECORD_NUMBER`` (regex, always)
Phone number                 ``PHONE_NUMBER`` (both backends)
Email address                ``EMAIL_ADDRESS``(both backends)
Explicit date                ``DATE_TIME``    (Presidio only)
===========================  ==========================================

Design notes
------------
* Standard library only, deterministic for a given ``--seed`` so the committed CSV is
  reproducible.
* **Idempotent** — if ``ReasonForVisitNote`` is already present the script exits without
  touching the file.
* Only ``--fill-rate`` of rows get a note (default 20%), because real encounter tables are
  sparsely noted and it keeps the committed CSV to a sane size. Empty notes also prove the
  redaction path handles NULL/blank safely.

Examples
--------
Backfill the committed sample data::

    python scripts/add_clinical_notes.py

Denser notes, different draw::

    python scripts/add_clinical_notes.py --fill-rate 0.5 --seed 7
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import tempfile
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "sample_data" / "caboodle_provider"

NOTE_COLUMN = "ReasonForVisitNote"

# Reserved-for-fiction phone range (555-01xx) and example.com so nothing is dialable/routable.
_AREA_CODES = ["212", "347", "415", "617", "718", "914"]

_COMPLAINTS = [
    "chest pain",
    "shortness of breath",
    "persistent cough",
    "lower back pain",
    "migraine",
    "fever and chills",
    "abdominal pain",
    "dizziness",
    "joint swelling",
    "fatigue",
    "elevated blood pressure",
    "medication review",
]

# Each template plants a different mix of identifiers so no single detector "solves" the demo.
# MRN values already carry an "MRN" prefix (e.g. MRN00000002), so templates never add one.
_TEMPLATES = [
    "Pt {name} ({mrn}) reports {complaint}; callback {phone}.",
    "Spoke with {name} on {date} re: {complaint}. Records to {email}.",
    "{name} presented with {complaint}. Contact {phone} for follow-up.",
    "Triage note: {name}, {mrn}, {complaint}. Confirmed by phone {phone}.",
    "Discharge summary for {name} - {complaint} resolved. Send copy to {email}.",
    "Follow-up scheduled {date} for {name} ({complaint}). Best number {phone}.",
]


def _rand_phone(rng: random.Random) -> str:
    return f"{rng.choice(_AREA_CODES)}-555-{rng.randint(100, 199):04d}"


def _rand_email(rng: random.Random, name: str) -> str:
    handle = name.strip().lower().replace(" ", ".").replace(",", "")
    return f"{handle}{rng.randint(1, 99)}@example.com"


def _rand_date(rng: random.Random) -> str:
    return f"{rng.randint(2024, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _load_patients(data_dir: Path) -> dict[str, tuple[str, str]]:
    """Return ``{PatientKey: (PatientName, MRN)}`` from the synthetic patient dimension."""
    path = data_dir / "DimPatient.csv"
    if not path.is_file():
        sys.exit(f"ERROR: {path} not found.")
    patients: dict[str, tuple[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            key = row.get("PatientKey", "")
            if key and key not in patients:
                patients[key] = (row.get("PatientName", ""), row.get("MRN", ""))
    return patients


def build_note(rng: random.Random, name: str, mrn: str) -> str:
    """Compose one synthetic note that plants several HIPAA identifiers in narrative text."""
    template = rng.choice(_TEMPLATES)
    return template.format(
        name=name,
        mrn=mrn,
        complaint=rng.choice(_COMPLAINTS),
        phone=_rand_phone(rng),
        email=_rand_email(rng, name),
        date=_rand_date(rng),
    )


def add_notes(data_dir: Path, fill_rate: float, seed: int) -> int:
    """Add ``ReasonForVisitNote`` to FactEncounter.csv. Returns the number of notes written."""
    enc_path = data_dir / "FactEncounter.csv"
    if not enc_path.is_file():
        sys.exit(f"ERROR: {enc_path} not found.")

    with enc_path.open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    if NOTE_COLUMN in header:
        print(f"{NOTE_COLUMN} already present in {enc_path.name} - nothing to do.")
        return 0

    patients = _load_patients(data_dir)
    print(f"Loaded {len(patients):,} synthetic patients for note text.")

    rng = random.Random(seed)  # noqa: S311 - synthetic sample data, not a cryptographic use
    written = 0
    total = 0

    # Write to a temp file in the same directory, then atomically replace.
    fd, tmp_name = tempfile.mkstemp(dir=str(data_dir), suffix=".tmp", text=True)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with (
            enc_path.open(newline="", encoding="utf-8") as src,
            tmp_path.open("w", newline="", encoding="utf-8") as dst,
        ):
            reader = csv.DictReader(src)
            fieldnames = list(reader.fieldnames or []) + [NOTE_COLUMN]
            # The committed sample CSVs use LF; csv.writer defaults to CRLF, which would
            # rewrite every line and leave the dataset with mixed endings.
            writer = csv.DictWriter(dst, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for row in reader:
                total += 1
                note = ""
                if rng.random() < fill_rate:
                    name, mrn = patients.get(row.get("PatientKey", ""), ("", ""))
                    if name:
                        note = build_note(rng, name, mrn)
                        written += 1
                row[NOTE_COLUMN] = note
                writer.writerow(row)
        tmp_path.replace(enc_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    print(f"Wrote {written:,} notes across {total:,} encounter rows -> {enc_path.name}")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument(
        "--fill-rate",
        type=float,
        default=0.2,
        help="fraction of encounter rows that receive a note (default 0.2)",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not 0.0 < args.fill_rate <= 1.0:
        sys.exit("ERROR: --fill-rate must be in (0, 1].")
    add_notes(args.data_dir, args.fill_rate, args.seed)


if __name__ == "__main__":
    main()
