"""Bounded current-release county observation access for public V1."""

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

from snowflake.connector import SnowflakeConnection
from snowflake.connector.cursor import SnowflakeCursor

if TYPE_CHECKING:
    from .cdc_surveillance import CDCObservationAdapter
from .climate_timing import phase
from .config import ApiSettings
from .county_context import context_observation, context_query
from .county_context_ids import CONTEXT_CONTRACT, CONTEXT_FIELDS
from .dependency_telemetry import connect
from .environmental_context import MEASURES, EnvironmentalRepository, environmental_observation
from .public_contract import (
    CollectionEnvelope,
    CollectionLinks,
    CollectionMeta,
    CountyContextObservation,
    EvidenceReference,
    GeographyIdentity,
    GeographyType,
    Observation,
    ObservationCollection,
    ObservationQuery,
    PublicObservation,
    PublicQueryError,
)
from .public_tokens import decode, encode
from .repository import AtlasDataUnavailableError, _sql_identifier
from .surveillance_delivery import SurveillanceResolver, resolve_context


class ObservationRepository(Protocol):
    def current_release(self) -> str: ...
    def measure_exists(self, measure_id: str, release: str) -> bool: ...
    def query(
        self, query: ObservationQuery, release: str, offset: int
    ) -> list[tuple[Any, ...]]: ...


class SnowflakeObservationRepository:
    def __init__(
        self, settings: ApiSettings, *, _connection: SnowflakeConnection | None = None
    ) -> None:
        self.settings = settings
        self._connection = _connection

    @contextmanager
    def climate_request(self) -> Iterator["SnowflakeObservationRepository"]:
        """One exclusively owned session for one synchronous request, never a pool."""
        body_error: BaseException | None = None
        try:
            with connect(self.settings) as connection:
                try:
                    yield SnowflakeObservationRepository(self.settings, _connection=connection)
                except BaseException as error:
                    body_error = error
                    raise
        except Exception as exc:
            if body_error is not None:
                if exc is body_error:
                    raise
                raise body_error from None
            raise AtlasDataUnavailableError("Governed observations are unavailable") from exc

    @contextmanager
    def _cursor(self) -> Iterator[SnowflakeCursor]:
        if self._connection is not None:
            with self._connection.cursor() as cursor:
                yield cursor
        else:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                yield cursor

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
            with self._cursor() as cursor:
                cursor.execute(
                    f"SELECT RELEASE_ID FROM {schema}.CURRENT_RELEASE_V",
                    timeout=self.settings.public_query_timeout_seconds,
                )
                row = cursor.fetchone()
            if row is None:
                raise AtlasDataUnavailableError("No current governed release")
            return str(row[0])
        except AtlasDataUnavailableError:
            raise
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed observations are unavailable") from exc

    def measure_exists(self, measure_id: str, release: str) -> bool:
        if measure_id in MEASURES:
            return EnvironmentalRepository(
                self.settings, _connection=self._connection
            ).measure_exists(measure_id, release)
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    f"SELECT 1 FROM {self.view} "
                    "WHERE MEASURE_ID = %s AND RELEASE_VERSION = %s "
                    + ("AND context_contract_version=%s " if measure_id in CONTEXT_FIELDS else "")
                    + "LIMIT 1",
                    (measure_id, release, CONTEXT_CONTRACT)
                    if measure_id in CONTEXT_FIELDS
                    else (measure_id, release),
                    timeout=self.settings.public_query_timeout_seconds,
                )
                return cursor.fetchone() is not None
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed observations are unavailable") from exc

    def query(self, query: ObservationQuery, release: str, offset: int) -> list[tuple[Any, ...]]:
        if query.measure_id in MEASURES:
            return EnvironmentalRepository(self.settings, _connection=self._connection).query(
                query, release, offset
            )
        if query.measure_id in CONTEXT_FIELDS:
            statement, params = context_query(self.view, query, release, offset)
            try:
                with self._cursor() as cursor:
                    cursor.execute(
                        statement, params, timeout=self.settings.public_query_timeout_seconds
                    )
                    return cursor.fetchall()
            except Exception as exc:
                raise AtlasDataUnavailableError("Governed county context is unavailable") from exc
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
            with self._cursor() as cursor:
                cursor.execute(
                    statement, params, timeout=self.settings.public_query_timeout_seconds
                )
                return cursor.fetchall()
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed observations are unavailable") from exc


class ObservationService:
    def __init__(
        self,
        repository: ObservationRepository,
        surveillance_resolver: SurveillanceResolver | None = None,
        cdc_adapter: "CDCObservationAdapter | None" = None,
    ) -> None:
        self.repository = repository
        self.surveillance_resolver = surveillance_resolver
        self.cdc_adapter = cdc_adapter

    def search(
        self, query: ObservationQuery, *, checkpoint: Callable[[], None] | None = None
    ) -> ObservationCollection:
        check = checkpoint or (lambda: None)
        if query.geography_type != GeographyType.county:
            raise PublicQueryError("UNSUPPORTED_FILTER", "Only county geography is supported.")
        if query.stratification:
            raise PublicQueryError(
                "UNSUPPORTED_STRATIFICATION", "This release has no governed strata."
            )
        if isinstance(self.repository, SnowflakeObservationRepository) and (
            query.measure_id in CONTEXT_FIELDS
            or (
                query.measure_id in MEASURES
                and self.repository.settings.environmental_context_enabled
            )
        ):
            check()
            with self.repository.climate_request() as repository:
                return self._search(query, repository, check)
        return self._search(query, self.repository, check)

    def _search(
        self, query: ObservationQuery, repository: ObservationRepository, check: Callable[[], None]
    ) -> ObservationCollection:
        check()
        from .cdc_surveillance import IDENTITIES

        cdc = self.cdc_adapter is not None and query.measure_id in IDENTITIES
        if cdc:
            assert self.cdc_adapter is not None
            self.cdc_adapter.validate_query(query)
        with phase("release_lookup"):
            release = repository.current_release()
        check()
        with phase("measure_lookup"):
            measure_exists = cdc or repository.measure_exists(query.measure_id, release)
        if not measure_exists:
            raise PublicQueryError(
                "RESOURCE_NOT_FOUND", "Measure has no published county observations."
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                query.model_dump(
                    mode="json",
                    exclude={"page_token", "page_size"}
                    | ({"vintage_year"} if query.vintage_year is None else set()),
                ),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        offset = self._offset(query.page_token, fingerprint, release)
        check()
        items: list[PublicObservation]
        if cdc:
            assert self.cdc_adapter is not None
            items = [
                item for item in self.cdc_adapter.search(query, release, offset, checkpoint=check)
            ]
            more = len(items) > query.page_size
            items = items[: query.page_size]
        else:
            with phase("observation_query"):
                rows = repository.query(query, release, offset)
            more = len(rows) > query.page_size
            if query.measure_id in CONTEXT_FIELDS and not rows and offset == 0:
                raise PublicQueryError(
                    "RESOURCE_NOT_FOUND", "No admitted county context for this selection."
                )
            mapper = (
                context_observation
                if query.measure_id in CONTEXT_FIELDS
                else environmental_observation
                if query.measure_id in MEASURES
                else self._observation
            )
            with phase("mapping"):
                # Context identity checks include the lookahead row so duplicate
                # county observations cannot straddle a public page boundary.
                mapped_rows = (
                    rows if query.measure_id in CONTEXT_FIELDS else rows[: query.page_size]
                )
                items = [mapper(row) for row in mapped_rows]
                if query.measure_id in CONTEXT_FIELDS:
                    pairs = {(item.measure_id, item.geography.geography_id) for item in items}
                    if len(pairs) != len(items) or any(
                        item.measure_id != query.measure_id
                        or item.release_id != release
                        or item.geography.geography_id not in query.geography_id
                        for item in items
                    ):
                        raise AtlasDataUnavailableError("Inconsistent governed county context")
                    items = items[: query.page_size]
        check()
        if not cdc and self.surveillance_resolver is not None:
            for item in items:
                check()
                if isinstance(item, Observation):
                    item.surveillance_context = resolve_context(item, self.surveillance_resolver)
        token = None
        if more:
            token = self._token(fingerprint, release, offset + query.page_size)
        meta = CollectionMeta(next_page_token=token)
        if query.measure_id in CONTEXT_FIELDS:
            context_items: list[CountyContextObservation] = []
            for item in items:
                if not isinstance(item, CountyContextObservation):
                    raise AtlasDataUnavailableError("Unexpected context collection shape")
                context_items.append(item)
            return CollectionEnvelope[CountyContextObservation](
                data=context_items, meta=meta, links=CollectionLinks()
            )
        dated_items: list[Observation] = []
        for item in items:
            if not isinstance(item, Observation):
                raise AtlasDataUnavailableError("Unexpected dated collection shape")
            dated_items.append(item)
        return CollectionEnvelope[Observation](data=dated_items, meta=meta, links=CollectionLinks())

    @staticmethod
    def _token(fingerprint: str, release: str, offset: int) -> str:
        return encode({"query": fingerprint, "release": release, "offset": offset})

    @staticmethod
    def _offset(token: str | None, fingerprint: str, release: str) -> int:
        if token is None:
            return 0
        if len(token) > 2048:
            raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.")
        try:
            payload = decode(token)
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
        except (ValueError, KeyError, TypeError) as exc:
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
