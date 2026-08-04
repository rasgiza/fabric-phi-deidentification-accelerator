"""Gold-layer conformance spec: project de-identified silver into one PHI-free star.

SYNTHETIC-DATA-ONLY reference pattern.

``02b_silver_deid`` is source-agnostic -- it de-identifies whatever sources are present
without knowing a single table name. The **gold** layer cannot be that agnostic: turning
Caboodle's dimensional tables and Clarity's normalized transactional tables into one star
is a *modelling* decision, and modelling decisions have to be written down somewhere.

This module is that "somewhere". It is deliberately **declarative and Spark-free** so the
shape of the star can be unit-tested against ``config/deid_rules.yaml`` on a laptop, in CI,
without a Fabric runtime. The gold notebooks (``03b_gold_safe`` and its Analytics-workspace
twin) read this spec and do nothing but execute it -- so the two notebooks cannot drift
apart, and neither can drift away from the rulebook.

Why the projections are pinned here rather than ``SELECT *``
-----------------------------------------------------------
A ``SELECT *`` gold layer silently inherits every new upstream column. If someone adds
``PAT_EMAIL_2`` to a Clarity extract and forgets a rule for it, ``SELECT *`` publishes it
to Power BI. An explicit column list fails closed: an unruled column is simply never
projected, and :func:`iter_ruled_projections` lets CI assert that every column we *do*
project is both known to the rulebook and not suppressed.

Conforming the two schemas
--------------------------
The join between the schemas is the **MRN token**. ``dim_patient.MRN`` (Caboodle) and
``clarity_patient.PAT_MRN_ID`` (Clarity) share the ``mrn`` token namespace, so the same
human resolves to the same ``PT-...`` value in both. That is the entire reason the
accelerator uses keyed HMAC tokens instead of random surrogates, and it is what makes
``gold_safe_dim_patient`` a genuinely conformed dimension rather than two patient lists.

Grain, and what deliberately does *not* union
---------------------------------------------
``dim_patient`` is the only table that unions across sources, because it is the only one
where the two schemas describe the same entity at the same grain. Clarity's orders and
results have **no Caboodle equivalent**, so they land as their own facts at their own
grain and link back through the conformed ``PatientKey``. Forcing them into
``fact_encounter`` would invent a grain that neither source actually has.
"""

from __future__ import annotations

from typing import Iterator, Mapping, NamedTuple

# ---------------------------------------------------------------------------
# Conformance keys
# ---------------------------------------------------------------------------

#: Column on Caboodle ``dim_patient`` holding the shared-namespace MRN token.
CABOODLE_MRN_COLUMN = "MRN"

#: Column on Clarity ``clarity_patient`` holding the shared-namespace MRN token.
#: Tokenised with the *same* namespace/prefix as :data:`CABOODLE_MRN_COLUMN`, which is
#: what allows the two patient populations to be conformed.
CLARITY_MRN_COLUMN = "PAT_MRN_ID"

#: Token namespace both MRN columns must share. Asserted in tests -- if these ever drift
#: apart the union below silently produces duplicate patients instead of a conformed one.
SHARED_MRN_NAMESPACE = "mrn"

#: Clarity's internal patient token, carried on every Clarity fact. Resolved to the
#: conformed ``PatientKey`` via ``clarity_patient`` before the fact is published.
CLARITY_PATIENT_LINK = "PAT_ID"

#: Surrogate key of the conformed patient dimension.
PATIENT_KEY = "PatientKey"

#: Discriminator added to ``gold_safe_dim_patient`` so an analyst can always tell which
#: source system(s) a patient row came from: ``Caboodle``, ``Clarity``, or ``Both``.
SOURCE_SYSTEM_COLUMN = "SourceSystem"

SOURCE_CABOODLE = "Caboodle"
SOURCE_CLARITY = "Clarity"
SOURCE_BOTH = "Both"


class GoldTable(NamedTuple):
    """One gold table and the de-identified silver table it projects from.

    Attributes
    ----------
    source:
        ``silver_deid_`` table name (without the prefix) this gold table reads.
    columns:
        Columns projected verbatim.
    year_columns:
        ``{silver_column: gold_column}`` for columns run through the notebook's
        ``year_of()`` helper. Under Safe Harbor the upstream value is already an int year;
        under Expert Determination it is a shifted date. Either way gold exposes a year,
        so the published grain never depends on which profile was active.
    patient_link:
        When set, the Clarity patient token column on this fact that must be resolved to
        :data:`PATIENT_KEY` against the conformed patient dimension before publishing.
    """

    source: str
    columns: tuple[str, ...] = ()
    year_columns: Mapping[str, str] = {}
    patient_link: str | None = None

    @property
    def projected_columns(self) -> tuple[str, ...]:
        """Every upstream column this table reads, verbatim or year-generalised."""
        return tuple(self.columns) + tuple(self.year_columns)


# ---------------------------------------------------------------------------
# Caboodle star (unchanged shape -- pinned here so both gold notebooks share one source
# of truth instead of two hand-maintained copies)
# ---------------------------------------------------------------------------

CABOODLE_GOLD: dict[str, GoldTable] = {
    "dim_provider": GoldTable(
        source="dim_provider",
        columns=(
            "ProviderKey", "NPI", "ProviderFullName", "Credentials", "Gender",
            "ProviderType", "PrimarySpecialty", "ProviderStatus", "IsActive",
            "HireDate", "TerminationDate",
        ),
    ),
    "dim_department": GoldTable(
        source="dim_department",
        columns=("DepartmentKey", "DepartmentName", "ServiceLine", "DepartmentSpecialty"),
    ),
    "dim_facility": GoldTable(
        source="dim_facility",
        columns=("FacilityKey", "FacilityName", "City", "StateAbbr", "ZIP", "Region"),
    ),
    "dim_payer": GoldTable(
        source="dim_payer",
        columns=("PayerKey", "PayerName", "PlanType", "LineOfBusiness"),
    ),
    "dim_diagnosis": GoldTable(
        source="dim_diagnosis",
        columns=("DiagnosisKey", "ICD10Code", "DiagnosisName", "DiagnosisCategory"),
    ),
    "dim_procedure": GoldTable(
        source="dim_procedure",
        columns=("ProcedureKey", "ProcedureCode", "CodeType", "ProcedureDescription"),
    ),
    "fact_encounter": GoldTable(
        source="fact_encounter",
        columns=(
            "EncounterKey", "PatientKey", "AttendingProviderKey", "ReferringProviderKey",
            "DepartmentKey", "LocationKey", "DiagnosisKey", "EncounterType", "LengthOfStay",
        ),
        year_columns={"EncounterDate": "EncounterYear"},
    ),
    "fact_claim": GoldTable(
        source="fact_claim",
        columns=(
            "ClaimKey", "PatientKey", "BillingProviderKey", "RenderingProviderKey",
            "PayerKey", "ProcedureKey", "DiagnosisKey", "BilledAmount", "AllowedAmount",
            "PaidAmount", "PatientResponsibility", "AllowedVariance", "ClaimStatus",
            "DeniedFlag",
        ),
        year_columns={"ServiceDate": "ServiceYear"},
    ),
    "fact_risk_score": GoldTable(
        source="fact_risk_score",
        columns=("RiskScoreKey", "PatientKey", "ProviderKey", "RiskModel", "RiskScore"),
        year_columns={"ScoreDate": "ScoreYear"},
    ),
}

# ---------------------------------------------------------------------------
# Conformed patient dimension -- the one table that unions across both schemas
# ---------------------------------------------------------------------------

#: Caboodle's contribution to ``gold_safe_dim_patient``.
PATIENT_FROM_CABOODLE = GoldTable(
    source="dim_patient",
    columns=(
        "PatientKey", "MRN", "PatientName", "Age", "AgeBand", "Gender", "Race",
        "Ethnicity", "ZIP", "PCPProviderKey",
    ),
    year_columns={"DateOfBirth": "BirthYear"},
)

#: Clarity's contribution, aligned to the same output columns.
#:
#: ``Race``/``Ethnicity``/``PCPProviderKey`` have no Clarity equivalent and are emitted as
#: typed NULLs rather than being dropped -- a conformed dimension must keep one schema, and
#: a NULL that means "this source doesn't carry it" is honest. ``PatientKey`` is *not*
#: listed: Caboodle's surrogate key does not exist in Clarity, so it is minted
#: deterministically from the MRN token (see :data:`PATIENT_KEY_SOURCE_COLUMN`).
PATIENT_FROM_CLARITY = GoldTable(
    source="clarity_patient",
    columns=(
        "PAT_ID", "PAT_MRN_ID", "PAT_NAME", "AGE", "AGE_BAND", "SEX_C", "ZIP",
    ),
    year_columns={"BIRTH_DATE": "BirthYear"},
)

#: ``{gold_column: clarity_column}``. Anything absent is NULL for Clarity-sourced rows.
PATIENT_CLARITY_RENAMES: Mapping[str, str] = {
    "MRN": "PAT_MRN_ID",
    "PatientName": "PAT_NAME",
    "Age": "AGE",
    "AgeBand": "AGE_BAND",
    "Gender": "SEX_C",
    "ZIP": "ZIP",
}

#: Gold columns with no Clarity source, emitted as NULL for Clarity-only patients.
PATIENT_CLARITY_NULLS: tuple[str, ...] = ("Race", "Ethnicity", "PCPProviderKey")

#: Clarity-only patients get a ``PatientKey`` hashed from this column. It must be the
#: shared-namespace MRN token, so a patient who later appears in Caboodle keeps the same
#: key instead of forking into a second row.
PATIENT_KEY_SOURCE_COLUMN = CLARITY_MRN_COLUMN

#: Final column order of ``gold_safe_dim_patient``.
PATIENT_GOLD_COLUMNS: tuple[str, ...] = (
    "PatientKey", "MRN", "ClarityPatientID", "PatientName", "BirthYear", "Age", "AgeBand",
    "Gender", "Race", "Ethnicity", "ZIP", "PCPProviderKey", SOURCE_SYSTEM_COLUMN,
)

# ---------------------------------------------------------------------------
# Clarity facts -- own grain, linked through the conformed PatientKey
# ---------------------------------------------------------------------------

CLARITY_GOLD: dict[str, GoldTable] = {
    "dim_clarity_provider": GoldTable(
        source="clarity_ser",
        columns=("PROV_ID", "PROV_NAME", "PROV_TYPE_C", "SPECIALTY", "NPI", "ACTIVE_STATUS"),
    ),
    "fact_clarity_encounter": GoldTable(
        source="clarity_pat_enc",
        columns=(
            "PAT_ENC_CSN_ID", "PAT_ID", "ENC_TYPE_C", "APPT_STATUS_C", "DEPARTMENT_ID",
            "VISIT_PROV_ID", "ENC_CLOSED_YN",
        ),
        # ENC_MONTH is suppressed by the rulebook (yyyy-MM is finer than Safe Harbor
        # allows), so the encounter grain in gold is the year -- never the month.
        year_columns={"PAT_ENC_DATE": "EncounterYear"},
        patient_link=CLARITY_PATIENT_LINK,
    ),
    "fact_clarity_admission": GoldTable(
        source="clarity_pat_enc_hsp",
        columns=(
            "PAT_ENC_CSN_ID", "PAT_ID", "ADT_PAT_CLASS", "ADMIT_SOURCE_C", "DISCH_DISP_C",
            "DEPARTMENT_ID", "ADMISSION_PROV_ID", "LOS_DAYS",
        ),
        # LOS_DAYS survives as-is: a length of stay is an interval, not a calendar date,
        # so it carries no Safe Harbor date risk while keeping the fact analytically useful.
        year_columns={"HOSP_ADMSN_TIME": "AdmitYear", "HOSP_DISCH_TIME": "DischargeYear"},
        patient_link=CLARITY_PATIENT_LINK,
    ),
    "fact_clarity_diagnosis": GoldTable(
        source="clarity_pat_enc_dx",
        columns=("PAT_ENC_CSN_ID", "PAT_ID", "LINE", "DX_ID", "PRIMARY_DX_YN", "DX_ED_YN_C"),
        year_columns={"CONTACT_DATE": "ContactYear"},
        patient_link=CLARITY_PATIENT_LINK,
    ),
    "fact_clarity_order_med": GoldTable(
        source="clarity_order_med",
        columns=(
            "ORDER_MED_ID", "PAT_ID", "PAT_ENC_CSN_ID", "MEDICATION_ID",
            "AUTHRZING_PROV_ID", "ORDER_STATUS_C", "QUANTITY", "HV_DISCRETE_DOSE",
        ),
        year_columns={
            "ORDERING_DATE": "OrderYear",
            "START_DATE": "StartYear",
            "END_DATE": "EndYear",
        },
        patient_link=CLARITY_PATIENT_LINK,
    ),
    "fact_clarity_order_proc": GoldTable(
        source="clarity_order_proc",
        columns=(
            "ORDER_PROC_ID", "PAT_ID", "PAT_ENC_CSN_ID", "PROC_ID", "AUTHRZING_PROV_ID",
            "ORDER_STATUS_C", "QUANTITY", "ORDER_TYPE",
        ),
        year_columns={"ORDERING_DATE": "OrderYear", "PROC_START_TIME": "ProcYear"},
        patient_link=CLARITY_PATIENT_LINK,
    ),
    "fact_clarity_result": GoldTable(
        source="clarity_order_results",
        columns=(
            "ORDER_PROC_ID", "LINE", "PAT_ID", "PAT_ENC_CSN_ID", "COMPONENT_ID",
            "ORD_VALUE", "ORD_NUM_VALUE", "REFERENCE_UNIT", "REFERENCE_LOW",
            "REFERENCE_HIGH", "RESULT_FLAG_C",
        ),
        year_columns={"RESULT_DATE": "ResultYear"},
        patient_link=CLARITY_PATIENT_LINK,
    ),
}

#: Every gold table name the publish gate must scan, in publish order.
GOLD_TABLES: tuple[str, ...] = (
    ("dim_patient",)
    + tuple(CABOODLE_GOLD)
    + tuple(CLARITY_GOLD)
)


def silver_dependencies(include_clarity: bool = True) -> tuple[str, ...]:
    """``silver_deid_`` tables (prefix omitted) required to build the gold star.

    Clarity is optional: a deployment that only loaded Caboodle still gets a valid star,
    it just has no Clarity facts. Caboodle is not optional -- it defines the conformed
    dimensions everything else hangs off.
    """
    required = [PATIENT_FROM_CABOODLE.source]
    required += [t.source for t in CABOODLE_GOLD.values()]
    if include_clarity:
        required.append(PATIENT_FROM_CLARITY.source)
        required += [t.source for t in CLARITY_GOLD.values()]
    # dict.fromkeys preserves order while de-duplicating.
    return tuple(dict.fromkeys(required))


def iter_ruled_projections(
    ruled_tables: Mapping[str, object],
) -> Iterator[tuple[str, str, str]]:
    """Yield ``(gold_table, silver_table, column)`` for projections the rulebook covers.

    Reference tables such as ``dim_payer`` carry no PHI and are intentionally absent from
    ``deid_rules.yaml``; skipping them here keeps CI focused on the projections where a
    missing or wrong rule would actually leak something.
    """
    everything: dict[str, GoldTable] = {
        "dim_patient(caboodle)": PATIENT_FROM_CABOODLE,
        "dim_patient(clarity)": PATIENT_FROM_CLARITY,
        **CABOODLE_GOLD,
        **CLARITY_GOLD,
    }
    for gold_name, spec in everything.items():
        if spec.source not in ruled_tables:
            continue
        for column in spec.projected_columns:
            yield gold_name, spec.source, column
