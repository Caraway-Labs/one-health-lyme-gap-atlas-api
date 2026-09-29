"""Bounded current-release county observation access for public V1."""

import base64
import binascii
import hashlib
import json
from datetime import date
from typing import Any, Protocol

from lyme_gap_atlas_shared.snowflake import connect

from .config import ApiSettings
from .public_contract import (
    CollectionEnvelope,
    CollectionLinks,
    CollectionMeta,
    EvidenceReference,
    GeographyIdentity,
    GeographyType,
    Observation,
    ObservationQuery,
    PublicQueryError,
)
from .repository import AtlasDataUnavailableError, _sql_identifier


class ObservationRepository(Protocol):
    def current_release(self) -> str: ...
    def measure_exists(self, measure_id: str, release: str) -> bool: ...
    def query(
        self, query: ObservationQuery, release: str, offset: int
    ) -> list[tuple[Any, ...]]: ...


class SnowflakeObservationRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    @property
    def view(self) -> str:
        return (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}."
            "CURRENT_COUNTY_OBSERVATIONS_V"
        )

    def current_release(self) -> str:
        schema = (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}"
        )
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT RELEASE_ID FROM {schema}.CURRENT_RELEASE_V")
                row = cursor.fetchone()
            if row is None:
                raise AtlasDataUnavailableError("No current governed release")
            return str(row[0])
        except AtlasDataUnavailableError:
            raise
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed observations are unavailable") from exc

    def measure_exists(self, measure_id: str, release: str) -> bool:
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT 1 FROM {self.view} "
                    "WHERE MEASURE_ID = %s AND RELEASE_VERSION = %s LIMIT 1",
                    (measure_id, release),
                )
                return cursor.fetchone() is not None
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed observations are unavailable") from exc

    def query(self, query: ObservationQuery, release: str, offset: int) -> list[tuple[Any, ...]]:
        fips = sorted(query.geography_id)
        placeholders = ", ".join(["%s"] * len(fips))
        start = date(query.year, 1, 1) if query.year is not None else query.start_date
        end = date(query.year, 12, 31) if query.year is not None else query.end_date
        statement = (
            "SELECT OBSERVATION_ID, MEASURE_ID, GEOGRAPHY_ID, GEOGRAPHY_TYPE, "
            "PERIOD_START, PERIOD_END, TEMPORAL_GRAIN, TO_JSON(VALUE), VALUE_STATE, "
            "UNIT, DENOMINATOR, SUPPORTED_STRATIFICATIONS, SEMANTIC_CONTRACT_VERSION, "
            "RELEASE_VERSION, SOURCE_KEY, SOURCE_LABEL, SOURCE_VINTAGE, SOURCE_URL, "
            "RETRIEVED_AT, TRANSFORMATION_VERSION, METHODOLOGY, "
            "RELEASE_METHODOLOGY_VERSION, OBSERVATION_LIMITATIONS, "
            "MEASURE_LIMITATION, RELEASE_LIMITATIONS, SOURCE_ID, DATASET_ID, METHODOLOGY_ID "
            f"FROM {self.view} WHERE MEASURE_ID = %s AND COUNTY_FIPS IN ({placeholders}) "
            "AND PERIOD_START >= %s AND PERIOD_END <= %s AND RELEASE_VERSION = %s "
            "ORDER BY MEASURE_ID, COUNTY_FIPS, PERIOD_START, OBSERVATION_ID "
            "LIMIT %s OFFSET %s"
        )
        params = (query.measure_id, *fips, start, end, release, query.page_size + 1, offset)
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(statement, params)
                return cursor.fetchall()
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed observations are unavailable") from exc


class ObservationService:
    def __init__(self, repository: ObservationRepository) -> None:
        self.repository = repository

    def search(self, query: ObservationQuery) -> CollectionEnvelope[Observation]:
        if query.geography_type != GeographyType.county:
            raise PublicQueryError("UNSUPPORTED_FILTER", "Only county geography is supported.")
        if query.stratification:
            raise PublicQueryError(
                "UNSUPPORTED_STRATIFICATION", "This release has no governed strata."
            )
        release = self.repository.current_release()
        if not self.repository.measure_exists(query.measure_id, release):
            raise PublicQueryError(
                "RESOURCE_NOT_FOUND", "Measure has no published county observations."
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                query.model_dump(mode="json", exclude={"page_token", "page_size"}), sort_keys=True
            ).encode()
        ).hexdigest()
        offset = self._offset(query.page_token, fingerprint, release)
        rows = self.repository.query(query, release, offset)
        items = [self._observation(row) for row in rows[: query.page_size]]
        token = None
        if len(rows) > query.page_size:
            token = self._token(fingerprint, release, offset + query.page_size)
        return CollectionEnvelope(
            data=items, meta=CollectionMeta(next_page_token=token), links=CollectionLinks()
        )

    @staticmethod
    def _token(fingerprint: str, release: str, offset: int) -> str:
        payload = json.dumps({"query": fingerprint, "release": release, "offset": offset})
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @staticmethod
    def _offset(token: str | None, fingerprint: str, release: str) -> int:
        if token is None:
            return 0
        if len(token) > 2048:
            raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.")
        try:
            payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
            offset = payload["offset"]
            if (
                payload["query"] != fingerprint
                or payload["release"] != release
                or type(offset) is not int
                or offset < 1
                or offset > 10_000
            ):
                raise ValueError
            return offset
        except (ValueError, KeyError, TypeError, UnicodeError, binascii.Error) as exc:
            raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.") from exc

    @staticmethod
    def _observation(row: tuple[Any, ...]) -> Observation:
        (
            observation_id,
            measure_id,
            geography_id,
            geography_type,
            period_start,
            period_end,
            temporal_grain,
            value_json,
            value_state,
            unit,
            denominator,
            supported_strata,
            semantic_version,
            release,
            source_id,
            source_label,
            source_vintage,
            source_url,
            retrieved_at,
            transformation_version,
            methodology,
            release_methodology_version,
            observation_limitation,
            measure_limitation,
            release_limitation,
            lineage_source_id,
            dataset_id,
            methodology_id,
        ) = row
        if (
            geography_type != "COUNTY"
            or supported_strata is not None
            or not lineage_source_id
            or not dataset_id
            or not methodology_id
        ):
            raise AtlasDataUnavailableError("Unsupported governed observation shape")
        try:
            return Observation(
                observation_id=observation_id,
                measure_id=measure_id,
                geography=GeographyIdentity(
                    geography_type=GeographyType.county, geography_id=geography_id
                ),
                period_start=period_start,
                period_end=period_end,
                temporal_grain=temporal_grain,
                value=json.loads(value_json) if value_json is not None else None,
                value_state=value_state,
                unit=unit,
                denominator=denominator,
                source_id=source_id,
                lineage_source_id=lineage_source_id,
                dataset_id=dataset_id,
                methodology_id=methodology_id,
                methodology=methodology,
                methodology_version=transformation_version,
                release_methodology_version=release_methodology_version,
                semantic_version=semantic_version,
                release_id=release,
                provenance_ref=observation_id,
                source_label=source_label,
                source_vintage=source_vintage,
                source_url=source_url,
                atlas_acquired_at=retrieved_at,
                limitations=[
                    x for x in (observation_limitation, measure_limitation, release_limitation) if x
                ],
                evidence=EvidenceReference(
                    resource_type="observation",
                    resource_id=observation_id,
                    source_id=source_id,
                    provenance_ref=observation_id,
                    semantic_version=semantic_version,
                    methodology_version=transformation_version,
                    release_id=release,
                ),
            )
        except (ValueError, TypeError) as exc:
            raise AtlasDataUnavailableError("Unsupported governed observation state") from exc
