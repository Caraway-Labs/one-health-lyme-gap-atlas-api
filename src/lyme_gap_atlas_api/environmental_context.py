"""January county-day adapter over the reviewed presentation boundary (API84)."""

import hashlib
import json
import math
from datetime import date
from decimal import Decimal
from typing import Any

from .config import ApiSettings
from .dependency_telemetry import connect
from .public_contract import (
    EnvironmentalContext,
    EvidenceReference,
    GeographyIdentity,
    GeographyType,
    Observation,
    ObservationQuery,
)
from .repository import AtlasDataUnavailableError, _sql_identifier

MEASURES = frozenset(f"nclimgrid_{m}_county_day" for m in ("prcp", "tmin", "tmax", "tavg"))
NUMERIC_FIELDS = (
    "value",
    "expected_area_m2",
    "intersected_area_m2",
    "source_supported_area_m2",
    "valid_area_m2",
    "source_coverage_fraction",
    "valid_fraction_of_supported_area",
)


def limitation_texts(value: Any) -> list[str]:
    try:
        items = json.loads(value) if isinstance(value, str) else value
        if (
            not isinstance(items, list)
            or not items
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get("text"), str)
                or not item["text"].strip()
                for item in items
            )
        ):
            raise ValueError("ENVIRONMENTAL_LIMITATIONS")
        return [item["text"] for item in items]
    except (ValueError, TypeError, KeyError) as exc:
        raise AtlasDataUnavailableError("Unsupported governed environmental limitations") from exc


FIELDS = (
    "release_id",
    "measure_id",
    "semantic_version",
    "metadata_revision_id",
    "county_fips",
    "period_start",
    "period_end",
    "temporal_resolution",
    "day_convention",
    "value",
    "value_state",
    "unit",
    "denominator",
    "coverage_status",
    "source_time_present",
    *NUMERIC_FIELDS[1:],
    *(
        f"{field}_{suffix}"
        for field in NUMERIC_FIELDS
        for suffix in ("stored_type", "native_double")
    ),
    "atlas_acquired_at",
    "source_published_at",
    "upstream_date_modified",
    "publisher",
    "dataset_id",
    "source_vintage",
    "methodology_version",
    "weight_version",
    "geometry_version",
    "limitations",
)


def decode_numerics(row: dict[str, Any]) -> dict[str, Any]:
    """DOUBLE uses native connector float; DECIMAL uses exact decimal text.

    Transport helper columns never reach HTTP. Decimal text is explicitly typed
    in the additive support DTO, avoiding silent IEEE-754 rounding.
    """
    result = dict(row)
    for field in NUMERIC_FIELDS:
        kind = result.pop(f"{field}_stored_type")
        native = result.pop(f"{field}_native_double")
        raw = result[field]
        if kind == "DOUBLE":
            if type(native) is not float or not math.isfinite(native):
                raise ValueError("ENVIRONMENTAL_NATIVE_DOUBLE")
            value: Any = native
        else:
            if native is not None:
                raise ValueError("ENVIRONMENTAL_NATIVE_TYPE")
            value = json.loads(raw, parse_float=Decimal) if isinstance(raw, str) else raw
            if kind in (None, "NULL_VALUE"):
                if raw is not None:
                    raise ValueError("ENVIRONMENTAL_SQL_NULL")
                value = None
            elif kind == "INTEGER":
                if type(value) is not int:
                    raise ValueError("ENVIRONMENTAL_INTEGER")
            elif kind == "DECIMAL":
                if type(value) is int:
                    value = Decimal(value)
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise ValueError("ENVIRONMENTAL_DECIMAL")
            else:
                raise ValueError("ENVIRONMENTAL_NUMERIC_TYPE")
        result[field] = str(value) if isinstance(value, Decimal) and value != 0 else value
    return result


class EnvironmentalRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    @property
    def schema(self) -> str:
        database = _sql_identifier(self.settings.presentation_database)
        schema = _sql_identifier(self.settings.snowflake_presentation_schema)
        return f"{database}.{schema}"

    def _read(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        if not self.settings.environmental_context_enabled:
            raise AtlasDataUnavailableError("Environmental context has not been enabled")
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(sql, params, timeout=self.settings.public_query_timeout_seconds)
                return cursor.fetchall()
        except Exception as exc:
            raise AtlasDataUnavailableError(
                "Governed environmental context is unavailable"
            ) from exc

    def measure_exists(self, measure_id: str, release: str) -> bool:
        return bool(
            self._read(
                f"SELECT 1 FROM {self.schema}.CURRENT_CLIMATE_MEASURE_METADATA_V "
                "WHERE measure_id=%s AND release_id=%s LIMIT 1",
                (measure_id, release),
            )
        )

    def metadata(self) -> list[tuple[Any, ...]]:
        return self._read(
            "SELECT "
            "release_id,measure_id,indicator_id,semantic_version,metadata_revision_id,label,"
            "definition,"
            "unit,denominator,geography_grain,temporal_resolution,allowed_value_states,"
            "methodology_version,limitations "
            f"FROM {self.schema}.CURRENT_CLIMATE_MEASURE_METADATA_V ORDER BY measure_id"
        )

    def query(self, query: ObservationQuery, release: str, offset: int) -> list[tuple[Any, ...]]:
        fips = sorted(query.geography_id)
        placeholders = ",".join(["%s"] * len(fips))
        columns = ",".join(FIELDS)
        start = date(query.year, 1, 1) if query.year is not None else query.start_date
        end = date(query.year, 12, 31) if query.year is not None else query.end_date
        return self._read(
            f"SELECT {columns} FROM {self.schema}.CURRENT_CLIMATE_COUNTY_DAY_OBSERVATIONS_V "
            f"WHERE measure_id=%s AND county_fips IN ({placeholders}) AND period_start>=%s "
            "AND period_end<=%s AND release_id=%s ORDER BY measure_id,county_fips,period_start "
            "LIMIT %s OFFSET %s",
            (query.measure_id, *fips, start, end, release, query.page_size + 1, offset),
        )


def environmental_observation(values: tuple[Any, ...]) -> Observation:
    try:
        if len(values) != len(FIELDS):
            raise ValueError("ENVIRONMENTAL_SHAPE")
        row = decode_numerics(dict(zip(FIELDS, values, strict=True)))
        if (
            row["measure_id"] not in MEASURES
            or row["temporal_resolution"] != "DAY"
            or row["period_start"] != row["period_end"]
            or not row["metadata_revision_id"]
            or row["methodology_version"] != "atlas-nclimgrid-county-day/2"
            or row["semantic_version"] != "2.0.0"
            or row["source_published_at"] is not None
        ):
            raise ValueError("ENVIRONMENTAL_SCOPE")
        limitations = limitation_texts(row["limitations"])
        expected_unit = (
            "mm" if row["measure_id"] == "nclimgrid_prcp_county_day" else "degree_Celsius"
        )
        if row["unit"] != expected_unit or row["dataset_id"] != "nclimgrid-daily-v1.0.0-scaled":
            raise ValueError("ENVIRONMENTAL_PRODUCT")
        coverage = row["coverage_status"]
        if (
            (coverage in {"PARTIAL_COVERAGE", "SOURCE_MISSING"} and row["value_state"] != "MISSING")
            or (coverage == "OUT_OF_SOURCE_COVERAGE" and row["value_state"] != "UNAVAILABLE")
            or (coverage == "COMPLETE" and row["value_state"] not in {"ZERO", "OBSERVED"})
        ):
            raise ValueError("ENVIRONMENTAL_COVERAGE_STATE")
        identity = hashlib.sha256(
            json.dumps(
                [
                    row[k].isoformat() if isinstance(row[k], date) else row[k]
                    for k in (
                        "release_id",
                        "metadata_revision_id",
                        "county_fips",
                        "period_start",
                        "measure_id",
                    )
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        reference = f"environmental-observation:{identity}"
        support = {k: row[k] for k in NUMERIC_FIELDS[1:]}
        support.update(
            {
                k: row[k]
                for k in (
                    "coverage_status",
                    "source_time_present",
                    "day_convention",
                    "upstream_date_modified",
                    "weight_version",
                    "geometry_version",
                    "metadata_revision_id",
                )
            }
        )
        return Observation(
            observation_id=reference,
            measure_id=row["measure_id"],
            geography=GeographyIdentity(
                geography_type=GeographyType.county, geography_id=row["county_fips"]
            ),
            period_start=row["period_start"],
            period_end=row["period_end"],
            temporal_grain="DAY",
            value=row["value"],
            value_state=row["value_state"],
            unit=row["unit"],
            denominator=row["denominator"],
            source_id="noaa_nclimgrid_daily_202501",
            lineage_source_id="noaa_nclimgrid_daily",
            dataset_id=row["dataset_id"],
            methodology_id=None,
            methodology="Valid-area-weighted retained NOAA grid over TIGER 2025 analysis counties.",
            methodology_version=row["methodology_version"],
            semantic_version=row["semantic_version"],
            release_id=row["release_id"],
            provenance_ref=reference,
            source_label=row["publisher"],
            source_vintage=row["source_vintage"],
            source_published_at=None,
            atlas_acquired_at=row["atlas_acquired_at"],
            limitations=limitations,
            environmental_context=EnvironmentalContext(**support),
            evidence=EvidenceReference(
                resource_type="observation",
                resource_id=reference,
                source_id="noaa_nclimgrid_daily_202501",
                provenance_ref=reference,
                semantic_version=row["semantic_version"],
                methodology_version=row["methodology_version"],
                release_id=row["release_id"],
            ),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise AtlasDataUnavailableError("Unsupported governed environmental observation") from exc
