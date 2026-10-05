"""Governed current-release metadata discovery, independent of HTTP transport."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from .config import ApiSettings
from .dependency_telemetry import connect
from .environmental_context import MEASURES, EnvironmentalRepository, limitation_texts
from .public_contract import GeographyType, Indicator, Measure, PublicQueryError
from .public_tokens import decode, encode
from .repository import AtlasDataUnavailableError, _sql_identifier


@dataclass(frozen=True)
class MetadataRows:
    indicators: list[tuple[Any, ...]]
    measures: list[tuple[Any, ...]]
    # None means disabled; an enabled empty view is unavailable, not annual-only.
    environmental_measures: list[tuple[Any, ...]] | None = None


MetadataItem = TypeVar("MetadataItem", Indicator, Measure)


class MetadataRepository(Protocol):
    def load_metadata(self, *, checkpoint: Callable[[], None] | None = None) -> MetadataRows: ...


class SnowflakeMetadataRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    def load_metadata(self, *, checkpoint: Callable[[], None] | None = None) -> MetadataRows:
        check = checkpoint or (lambda: None)
        schema = (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}"
        )
        try:
            check()
            with connect(self.settings) as connection, connection.cursor() as cursor:
                check()
                cursor.execute(
                    "SELECT INDICATOR_ID, LABEL, DESCRIPTION, LIMITATION, DOMAIN, CATEGORY, "
                    "SEMANTIC_CONTRACT_VERSION, RELEASE_VERSION "
                    f"FROM {schema}.CURRENT_INDICATOR_METADATA_V ORDER BY INDICATOR_ID",
                    timeout=self.settings.public_query_timeout_seconds,
                )
                check()
                indicators = cursor.fetchall()
                check()
                cursor.execute(
                    "SELECT MEASURE_ID, INDICATOR_ID, LABEL, DESCRIPTION, MEASURE_TYPE, "
                    "UNIT, DENOMINATOR, GEOGRAPHY_TYPE, TEMPORAL_GRAIN, "
                    "SUPPORTED_STRATIFICATIONS, SOURCE_REFERENCES, STANDARDS_MAPPINGS, "
                    "MISSINGNESS_SEMANTICS, METHODOLOGY, LIMITATION, "
                    "SEMANTIC_CONTRACT_VERSION, RELEASE_VERSION "
                    f"FROM {schema}.CURRENT_MEASURE_METADATA_V ORDER BY MEASURE_ID",
                    timeout=self.settings.public_query_timeout_seconds,
                )
                check()
                measures = cursor.fetchall()
            check()
            if self.settings.environmental_context_enabled:
                environmental_repository = EnvironmentalRepository(self.settings)
                environmental = (
                    environmental_repository.metadata()
                    if checkpoint is None
                    else environmental_repository.metadata(checkpoint=checkpoint)
                )
            else:
                environmental = None
            return MetadataRows(indicators, measures, environmental)
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed metadata is unavailable") from exc


class MetadataService:
    def __init__(self, repository: MetadataRepository) -> None:
        self.repository = repository

    def discover(
        self, *, checkpoint: Callable[[], None] | None = None
    ) -> tuple[list[Indicator], list[Measure]]:
        if checkpoint is None:
            rows = self.repository.load_metadata()
        else:
            checkpoint()
            rows = self.repository.load_metadata(checkpoint=checkpoint)
            checkpoint()
        if not rows.indicators or not rows.measures:
            raise AtlasDataUnavailableError("No current governed metadata release")
        relationships: dict[tuple[str, str], list[str]] = {}
        measures = []
        for row in rows.measures:
            (
                measure_id,
                indicator_id,
                label,
                description,
                measure_type,
                unit,
                denominator,
                geography_type,
                temporal_grain,
                strata,
                sources,
                mappings,
                missingness,
                methodology,
                limitation,
                schema_version,
                release_version,
            ) = row
            relationships.setdefault((indicator_id, release_version), []).append(measure_id)
            # The current release persists no governed strata, source relationships,
            # or standards mappings. Reject unexpected non-null projections until
            # their structured contract has been reviewed.
            if any(value is not None for value in (strata, sources, mappings)):
                raise AtlasDataUnavailableError("Unsupported governed metadata shape")
            measures.append(
                Measure(
                    measure_id=measure_id,
                    indicator_id=indicator_id,
                    label=label,
                    definition=description,
                    semantic_version=schema_version,
                    release_version=release_version,
                    measure_type=measure_type,
                    unit=unit,
                    denominator=denominator,
                    geography_semantics=geography_type,
                    temporal_semantics=temporal_grain,
                    missingness_semantics=missingness,
                    methodology=methodology,
                    limitations=[limitation] if limitation else [],
                )
            )
        indicators = [
            Indicator(
                indicator_id=row[0],
                label=row[1],
                definition=row[2],
                limitations=[row[3]] if row[3] else [],
                domain=row[4],
                category=row[5],
                semantic_version=row[6],
                release_version=row[7],
                measure_ids=sorted(relationships.get((row[0], row[7]), [])),
            )
            for row in rows.indicators
        ]
        releases = {(item.semantic_version, item.release_version) for item in indicators + measures}
        indicator_ids = {item.indicator_id for item in indicators}
        if (
            len(releases) != 1
            or len(indicator_ids) != len(indicators)
            or len({item.measure_id for item in measures}) != len(measures)
            or any(item.indicator_id not in indicator_ids for item in measures)
        ):
            raise AtlasDataUnavailableError("Governed metadata release is inconsistent")
        if rows.environmental_measures is not None:
            current_release = measures[0].release_version
            if (
                any(len(row) != 14 for row in rows.environmental_measures)
                or {row[1] for row in rows.environmental_measures} != MEASURES
            ):
                raise AtlasDataUnavailableError("Incomplete governed environmental metadata")
            climate_indicators: dict[str, list[str]] = {}
            for row in rows.environmental_measures:
                (
                    release,
                    measure_id,
                    indicator_id,
                    version,
                    revision,
                    label,
                    definition,
                    unit,
                    denominator,
                    grain,
                    temporal,
                    states,
                    method,
                    limitations,
                ) = row
                try:
                    states = json.loads(states) if isinstance(states, str) else states
                except (ValueError, TypeError) as exc:
                    raise AtlasDataUnavailableError("Invalid environmental value states") from exc
                limitations = limitation_texts(limitations)
                expected_indicator = (
                    "climate_precipitation"
                    if measure_id == "nclimgrid_prcp_county_day"
                    else "climate_temperature"
                )
                expected_unit = (
                    "mm" if measure_id == "nclimgrid_prcp_county_day" else "degree_Celsius"
                )
                if (
                    release != current_release
                    or measure_id not in MEASURES
                    or version != "2.0.0"
                    or not revision
                    or indicator_id != expected_indicator
                    or unit != expected_unit
                    or method != "atlas-nclimgrid-county-day/2"
                    or grain != "COUNTY"
                    or temporal != "DAY"
                    or not isinstance(limitations, list)
                    or not limitations
                    or not isinstance(states, list)
                    or set(states) != {"OBSERVED", "ZERO", "MISSING", "UNAVAILABLE"}
                ):
                    raise AtlasDataUnavailableError("Unsupported governed environmental metadata")
                climate_indicators.setdefault(indicator_id, []).append(measure_id)
                measures.append(
                    Measure(
                        measure_id=measure_id,
                        indicator_id=indicator_id,
                        label=label,
                        definition=definition,
                        semantic_version=version,
                        release_version=release,
                        measure_type="DERIVED",
                        unit=unit,
                        denominator=denominator,
                        geography_types=[GeographyType.county],
                        temporal_grains=["DAY"],
                        geography_semantics=grain,
                        temporal_semantics=temporal,
                        allowed_value_states=states,
                        source_ids=["noaa_nclimgrid_daily_202501"],
                        methodology=method,
                        limitations=limitations,
                    )
                )
            if len({r[1] for r in rows.environmental_measures}) != len(rows.environmental_measures):
                raise AtlasDataUnavailableError("Duplicate governed environmental measure")
            for indicator_id, ids in climate_indicators.items():
                if indicator_id in indicator_ids:
                    raise AtlasDataUnavailableError("Conflicting environmental indicator")
                indicators.append(
                    Indicator(
                        indicator_id=indicator_id,
                        label="Descriptive county weather",
                        definition="Reviewed county weather context; no disease-risk "
                        "interpretation.",
                        measure_ids=sorted(ids),
                        semantic_version="2.0.0",
                        release_version=current_release,
                        domain="climate",
                        limitations=["January 2025 descriptive county weather only."],
                    )
                )
        return sorted(indicators, key=lambda x: x.indicator_id), sorted(
            measures, key=lambda x: x.measure_id
        )

    @staticmethod
    def page(
        items: list[MetadataItem], size: int, token: str | None, query: dict[str, str | None]
    ) -> tuple[list[MetadataItem], str | None]:
        release = items[0].release_version if items else None
        offset = 0
        if token:
            if len(token) > 2048:
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.")
            try:
                payload = decode(token)
                if payload["query"] != query or payload["release"] != release:
                    raise ValueError
                offset = payload["offset"]
                if type(offset) is not int or offset < 0 or offset >= len(items):
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.") from exc
        end = offset + size
        next_token = None
        if end < len(items):
            payload = {"query": query, "release": release, "offset": end}
            next_token = encode(payload)
        return items[offset:end], next_token
