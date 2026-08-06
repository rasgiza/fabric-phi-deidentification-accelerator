"""Link the synthetic Clarity cohort to the synthetic Caboodle cohort by MRN.

WHY THIS EXISTS
---------------
The two sample datasets were generated independently, so their MRNs are disjoint by
construction -- Caboodle uses ``MRN%08d`` and Clarity uses ``MRN2%06d``. Their
intersection is exactly zero, which means the conformed patient dimension in ``03b``
can never emit ``SourceSystem = 'Both'`` and the cross-source conforming that the gold
star exists to demonstrate is silently untestable.

That is also the *opposite* of the real world: in Epic, Caboodle is built from Clarity,
so essentially every Clarity patient also appears in Caboodle. This script restores that
shape by overwriting ``PAT_MRN_ID`` in Clarity's ``PATIENT.csv`` so that a configurable
share of Clarity patients carry an MRN that a Caboodle patient also carries.

SAFETY / SCOPE
--------------
``PAT_MRN_ID`` appears in exactly one file (``PATIENT.csv``). Every other Clarity table
joins on ``PAT_ID``, which is untouched, so referential integrity is preserved.

DETERMINISM / IDEMPOTENCE
-------------------------
The assignment is keyed on the row's position in ``PATIENT.csv`` under a fixed seed and
never reads the current MRN value, so re-running is a no-op. Linked patients draw from a
seeded sample of Caboodle MRNs *without replacement*, so two Clarity patients can never
collapse onto one Caboodle patient (which would fan out the join in 03b).

Run from the repository root:

    python scripts/link_clarity_to_caboodle.py            # apply
    python scripts/link_clarity_to_caboodle.py --check    # report only, exit 1 if unlinked
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CABOODLE_PATIENTS = REPO / "sample_data" / "caboodle_provider" / "DimPatient.csv"
CLARITY_PATIENTS = REPO / "sample_data" / "Clarity" / "PATIENT.csv"

# Share of Clarity patients that also exist in Caboodle. Deliberately below 1.0 so the
# demo exercises all three SourceSystem values (Both / Caboodle / Clarity) rather than
# only two. Real Epic overlap is nearer 1.0.
LINK_FRACTION = 0.80
SEED = 20260805  # fixed so the sample data is reproducible from the CSVs in git


def _read_column(path: Path, column: str) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise SystemExit(f"{path.name}: expected a {column!r} column, got {reader.fieldnames}")
        return [row[column] for row in reader]


def plan_assignment() -> tuple[list[str | None], int]:
    """Return one entry per Clarity row: a Caboodle MRN to adopt, or None to stay unlinked."""
    caboodle_mrns = _read_column(CABOODLE_PATIENTS, "MRN")
    clarity_count = len(_read_column(CLARITY_PATIENTS, "PAT_MRN_ID"))

    link_count = int(round(clarity_count * LINK_FRACTION))
    # Not cryptographic: this shapes a synthetic fixture and MUST be reproducible, which
    # is the opposite of what secrets.SystemRandom provides.
    rng = random.Random(SEED)  # noqa: S311
    # Sample WITHOUT replacement: a Caboodle patient is claimed by at most one Clarity row.
    borrowed = rng.sample(sorted(set(caboodle_mrns)), link_count)
    linked_rows = set(rng.sample(range(clarity_count), link_count))

    assignment: list[str | None] = [None] * clarity_count
    # strict=True: the two sequences are built from the same link_count, so a length
    # mismatch means the sampling logic broke and some rows would silently go unlinked.
    for mrn, row in zip(borrowed, sorted(linked_rows), strict=True):
        assignment[row] = mrn
    return assignment, clarity_count


def apply_assignment(assignment: list[str | None]) -> None:
    with CLARITY_PATIENTS.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for index, row in enumerate(rows):
        borrowed = assignment[index]
        # Unlinked rows are rewritten to their original generated pattern rather than left
        # as-is, so the result depends only on (seed, row index) and re-running is a no-op.
        row["PAT_MRN_ID"] = borrowed if borrowed else f"MRN2{index:06d}"

    with CLARITY_PATIENTS.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def report() -> int:
    caboodle = set(_read_column(CABOODLE_PATIENTS, "MRN"))
    clarity = _read_column(CLARITY_PATIENTS, "PAT_MRN_ID")
    both = sorted(caboodle & set(clarity))

    if len(clarity) != len(set(clarity)):
        raise SystemExit("Clarity PAT_MRN_ID is not unique -- the 03b join would fan out.")

    print(f"caboodle patients : {len(caboodle):,}")
    print(f"clarity  patients : {len(clarity):,}")
    print("expected conformed dim_patient in 03b:")
    print(f"  SourceSystem='Both'     : {len(both):,}")
    print(f"  SourceSystem='Caboodle' : {len(caboodle) - len(both):,}")
    print(f"  SourceSystem='Clarity'  : {len(clarity) - len(both):,}")
    print(f"  total rows              : {len(caboodle) + len(clarity) - len(both):,}")
    return len(both)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report the current overlap without modifying any file"
    )
    args = parser.parse_args()

    if not args.check:
        assignment, _ = plan_assignment()
        apply_assignment(assignment)
        print(f"Rewrote PAT_MRN_ID in {CLARITY_PATIENTS.relative_to(REPO)}")

    overlap = report()
    if overlap == 0:
        print("\nFAIL: no shared MRNs -- 03b cannot produce SourceSystem='Both'.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
