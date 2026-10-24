"""Typed consumer mapping over Data's admitted existing observation projection."""

import json
from datetime import date
from decimal import Decimal
from typing import Any

from .county_context_ids import CONTEXT_CONTRACT, CONTEXT_FIELDS
from .public_contract import (
    CountyContext,
    CountyContextObservation,
    EvidenceReference,
    GeographyIdentity,
    GeographyType,
    ObservationQuery,
)
from .repository import AtlasDataUnavailableError

FIELDS = (
    "observation_id",
    "measure_id",
    "county_fips",
    "period_start",
    "period_end",
    "temporal_grain",
    "value_json",
    "value_state",
    "unit",
    "denominator",
    "release_version",
    "source_key",
    "source_label",
    "source_vintage",
    "source_url",
    "retrieved_at",
    "transformation_version",
    "release_methodology_version",
    "observation_limitations",
    "release_limitations",
    "source_id",
    "dataset_id",
    "semantic_version",
    "metadata_revision_id",
    "metadata_methodology_version",
    "vintage_year",
    "value_type",
    "category_label",
    "source_field",
    "definition",
    "allowed_value_states",
    "context_limitations",
    "source_published_at",
    "context_contract_version",
)
COLUMNS = ",".join("TO_JSON(value) AS value_json" if k == "value_json" else k for k in FIELDS)


def _view_column_list(alias: str) -> str:
    return ",".join(
        f"TO_JSON({alias}.value) AS value_json"
        if key == "value_json"
        else f"{alias}.{key}"
        for key in FIELDS
    )


def _outer_observation_columns() -> str:
    return ",".join(f"o.{key}" for key in FIELDS)


def context_query_with_release(
    view: str, release_schema: str, query: ObservationQuery, offset: int
) -> tuple[str, tuple[Any, ...]]:
    """One round-trip: governed release pointer plus optional context observation rows."""
    if query.measure_id not in CONTEXT_FIELDS:
        raise AtlasDataUnavailableError("Unsupported county context measure")
    fips = sorted(query.geography_id)
    identities = ",".join(["%s"] * len(CONTEXT_FIELDS))
    counties = ",".join(["%s"] * len(fips))
    view_cols = _view_column_list("v")
    outer_cols = _outer_observation_columns()
    statement = (
        "WITH release_rows AS ("
        f"SELECT RELEASE_ID FROM {release_schema}.CURRENT_RELEASE_V LIMIT 2"
        ") "
        f"SELECT rel.RELEASE_ID,{outer_cols} "
        "FROM release_rows rel "
        "LEFT JOIN LATERAL ("
        f"SELECT {view_cols} FROM {view} v "
        f"WHERE v.measure_id IN ({identities}) AND v.measure_id=%s "
        f"AND v.county_fips IN ({counties}) "
        "AND v.context_contract_version=%s AND v.vintage_year=%s "
        "AND v.release_version=rel.RELEASE_ID "
        "ORDER BY v.measure_id,v.county_fips,v.observation_id "
        "LIMIT %s OFFSET %s"
        ") o ON TRUE "
        "WHERE (SELECT COUNT(*) FROM release_rows)=1"
    )
    params: tuple[Any, ...] = (
        *CONTEXT_FIELDS,
        query.measure_id,
        *fips,
        CONTEXT_CONTRACT,
        query.vintage_year,
        query.page_size + 1,
        offset,
    )
    return statement, params


def parse_context_batch_rows(
    rows: list[tuple[Any, ...]],
) -> tuple[str, list[tuple[Any, ...]]]:
    if not rows:
        raise AtlasDataUnavailableError("No current governed release")
    release_id = rows[0][0]
    if not isinstance(release_id, str) or not release_id.strip():
        raise AtlasDataUnavailableError("No current governed release")
    if any(row[0] != release_id for row in rows):
        raise AtlasDataUnavailableError("No current governed release")
    observations: list[tuple[Any, ...]] = []
    for row in rows:
        if row[1] is not None:
            observations.append(row[1:])
    return release_id, observations


def context_query(
    view: str, query: ObservationQuery, release: str, offset: int
) -> tuple[str, tuple[Any, ...]]:
    if query.measure_id not in CONTEXT_FIELDS:
        raise AtlasDataUnavailableError("Unsupported county context measure")
    fips = sorted(query.geography_id)
    identities = ",".join(["%s"] * len(CONTEXT_FIELDS))
    counties = ",".join(["%s"] * len(fips))
    return (
        f"SELECT {COLUMNS} FROM {view} WHERE measure_id IN ({identities}) "
        f"AND measure_id=%s AND county_fips IN ({counties}) "
        "AND context_contract_version=%s AND vintage_year=%s AND release_version=%s "
        "ORDER BY measure_id,county_fips,observation_id LIMIT %s OFFSET %s",
        (
            *CONTEXT_FIELDS,
            query.measure_id,
            *fips,
            CONTEXT_CONTRACT,
            query.vintage_year,
            release,
            query.page_size + 1,
            offset,
        ),
    )


def context_observation(values: tuple[Any, ...]) -> CountyContextObservation:
    try:
        row = dict(zip(FIELDS, values, strict=True))
        mid = row["measure_id"]
        rucc = mid == "rucc_2023"
        svi = not rucc
        if (
            mid not in CONTEXT_FIELDS
            or row["source_field"] != CONTEXT_FIELDS[mid]
            or row["context_contract_version"] != CONTEXT_CONTRACT
            or not row["metadata_revision_id"]
            or not row["definition"]
            or row["semantic_version"] != ("2.0.0" if rucc else "1.0.0")
            or row["source_key"] != ("context_rucc" if rucc else "context_svi")
            or row["source_id"] != ("usda_ers" if rucc else "cdc_atsdr_svi")
            or row["dataset_id"]
            != ("rural-urban-continuum-codes-2023" if rucc else "atsdr-svi-2022-county-layer")
            or row["vintage_year"] != (2023 if rucc else 2022)
            or row["source_vintage"] != ("2023" if rucc else "2022 (2018-2022 ACS)")
            or row["source_published_at"] is not None
        ):
            raise ValueError("CONTEXT_SCOPE")
        if rucc:
            if (
                row["temporal_grain"] != "VINTAGE_YEAR"
                or row["period_start"] is not None
                or row["period_end"] is not None
                or not row["category_label"]
            ):
                raise ValueError("CONTEXT_VINTAGE")
        elif (
            row["temporal_grain"] != "PERIOD"
            or row["period_start"] != date(2018, 1, 1)
            or row["period_end"] != date(2022, 12, 31)
            or row["category_label"] is not None
        ):
            raise ValueError("CONTEXT_PERIOD")
        integer = mid in {"rucc_2023", "population_2022"}
        expected_unit = (
            "code"
            if rucc
            else "people"
            if integer
            else "percent"
            if mid == "uninsured_percent_2022"
            else "percentile"
        )
        if row["unit"] != expected_unit or row["value_type"] != (
            "integer" if integer else "number"
        ):
            raise ValueError("CONTEXT_UNIT")
        states = (
            json.loads(row["allowed_value_states"])
            if isinstance(row["allowed_value_states"], str)
            else row["allowed_value_states"]
        )
        if states != (["OBSERVED"] if rucc else ["OBSERVED", "ZERO", "MISSING"]):
            raise ValueError("CONTEXT_ALLOWED_STATES")
        state = row["value_state"]
        value = (
            json.loads(row["value_json"], parse_float=Decimal)
            if row["value_json"] is not None
            else None
        )
        if state not in states or (state == "MISSING" and value is not None):
            raise ValueError("CONTEXT_STATE")
        if state != "MISSING":
            if (
                isinstance(value, bool)
                or not isinstance(value, int | Decimal)
                or not Decimal(value).is_finite()
            ):
                raise ValueError("CONTEXT_NUMERIC")
            if (
                (integer and type(value) is not int)
                or (state == "ZERO" and value != 0)
                or (state == "OBSERVED" and value == 0)
            ):
                raise ValueError("CONTEXT_TYPE_STATE")
            if (
                (rucc and not 1 <= value <= 9)
                or (svi and value < 0)
                or (expected_unit == "percentile" and value > 1)
                or (expected_unit == "percent" and value > 100)
            ):
                raise ValueError("CONTEXT_DOMAIN")
        limitations = (
            json.loads(row["context_limitations"])
            if isinstance(row["context_limitations"], str)
            else row["context_limitations"]
        )
        if not isinstance(limitations, list) or not limitations:
            raise ValueError("CONTEXT_LIMITATIONS")
        texts = [item["text"] for item in limitations]
        if any(not isinstance(text, str) or not text for text in texts):
            raise ValueError("CONTEXT_LIMITATIONS")
        reference = row["observation_id"]
        return CountyContextObservation(
            observation_id=reference,
            measure_id=mid,
            geography=GeographyIdentity(
                geography_type=GeographyType.county, geography_id=row["county_fips"]
            ),
            period_start=row["period_start"],
            period_end=row["period_end"],
            temporal_grain=row["temporal_grain"],
            value=0 if value == 0 else str(value) if isinstance(value, Decimal) else value,
            value_state=state,
            unit=row["unit"],
            denominator=row["denominator"],
            source_id=row["source_key"],
            lineage_source_id=row["source_id"],
            dataset_id=row["dataset_id"],
            methodology_id=None,
            methodology=row["definition"],
            methodology_version=row["transformation_version"],
            release_methodology_version=row["release_methodology_version"],
            semantic_version=row["semantic_version"],
            metadata_revision_id=row["metadata_revision_id"],
            release_id=row["release_version"],
            provenance_ref=reference,
            source_label=row["source_label"],
            source_vintage=row["source_vintage"],
            source_url=row["source_url"],
            atlas_acquired_at=row["retrieved_at"],
            source_published_at=None,
            limitations=[
                *texts,
                *[row[k] for k in ("observation_limitations", "release_limitations") if row[k]],
            ],
            evidence=EvidenceReference(
                resource_type="observation",
                resource_id=reference,
                source_id=row["source_key"],
                provenance_ref=reference,
                semantic_version=row["semantic_version"],
                methodology_version=row["transformation_version"],
                release_id=row["release_version"],
            ),
            county_context=CountyContext(
                contract_version=row["context_contract_version"],
                vintage_year=row["vintage_year"],
                source_field=row["source_field"],
                value_type=row["value_type"],
                category_label=row["category_label"],
                definition=row["definition"],
                allowed_value_states=states,
                metadata_methodology_version=row["metadata_methodology_version"],
            ),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise AtlasDataUnavailableError("Unsupported governed county context observation") from exc
