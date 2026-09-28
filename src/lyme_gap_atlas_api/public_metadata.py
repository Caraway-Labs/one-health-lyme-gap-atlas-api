"""Governed current-release metadata discovery, independent of HTTP transport."""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from lyme_gap_atlas_shared.snowflake import connect

from .config import ApiSettings
from .public_contract import Indicator, Measure, PublicQueryError
from .repository import AtlasDataUnavailableError, _sql_identifier


@dataclass(frozen=True)
class MetadataRows:
    indicators: list[tuple[Any, ...]]
    measures: list[tuple[Any, ...]]


MetadataItem = TypeVar("MetadataItem", Indicator, Measure)


class MetadataRepository(Protocol):
    def load_metadata(self) -> MetadataRows: ...


class SnowflakeMetadataRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    def load_metadata(self) -> MetadataRows:
        schema = (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}"
        )
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT INDICATOR_ID, LABEL, DESCRIPTION, LIMITATION, DOMAIN, CATEGORY, "
                    "SEMANTIC_CONTRACT_VERSION, RELEASE_VERSION "
                    f"FROM {schema}.CURRENT_INDICATOR_METADATA_V ORDER BY INDICATOR_ID"
                )
                indicators = cursor.fetchall()
                cursor.execute(
                    "SELECT MEASURE_ID, INDICATOR_ID, LABEL, DESCRIPTION, MEASURE_TYPE, "
                    "UNIT, DENOMINATOR, GEOGRAPHY_TYPE, TEMPORAL_GRAIN, "
                    "SUPPORTED_STRATIFICATIONS, SOURCE_REFERENCES, STANDARDS_MAPPINGS, "
                    "MISSINGNESS_SEMANTICS, METHODOLOGY, LIMITATION, "
                    "SEMANTIC_CONTRACT_VERSION, RELEASE_VERSION "
                    f"FROM {schema}.CURRENT_MEASURE_METADATA_V ORDER BY MEASURE_ID"
                )
                measures = cursor.fetchall()
            return MetadataRows(indicators, measures)
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed metadata is unavailable") from exc


class MetadataService:
    def __init__(self, repository: MetadataRepository) -> None:
        self.repository = repository

    def discover(self) -> tuple[list[Indicator], list[Measure]]:
        rows = self.repository.load_metadata()
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
        return indicators, measures

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
                payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
                if payload["query"] != query or payload["release"] != release:
                    raise ValueError
                offset = payload["offset"]
                if type(offset) is not int or offset < 0 or offset >= len(items):
                    raise ValueError
            except (ValueError, KeyError, TypeError, binascii.Error) as exc:
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.") from exc
        end = offset + size
        next_token = None
        if end < len(items):
            payload = {"query": query, "release": release, "offset": end}
            next_token = (
                base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode())
                .decode()
                .rstrip("=")
            )
        return items[offset:end], next_token
