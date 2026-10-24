"""Bounded current-release county observation access for public V1."""

import asyncio
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
from .cdc_timing import phase as cdc_phase
from .climate_timing import phase
from .config import ApiSettings
from .context_timing import phase as context_phase
from .county_context import (
    context_observation,
    context_query,
    context_query_with_release,
    parse_context_batch_rows,
)
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
from .snowflake_read_session import get_read_session_pool, read_connection
from .surveillance_delivery import SurveillanceResolver, resolve_context
from .telemetry_logging import operational_logger

logger = operational_logger(__name__)


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
    def request_session(
        self, *, close_on_exit_error: bool = False
    ) -> Iterator["SnowflakeObservationRepository"]:
        """Borrow a process-scoped read connection for this worker thread."""
        try:
            with read_connection(self.settings) as borrowed:
                try:
                    yield type(self)(self.settings, _connection=borrowed)
                except BaseException:
                    if close_on_exit_error:
                        get_read_session_pool().drop_current()
                    raise
        except BaseException as exc:
            if isinstance(exc, (AtlasDataUnavailableError, PublicQueryError)):
                raise
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, TimeoutError)):
                raise
            if isinstance(exc, Exception):
                raise AtlasDataUnavailableError("Governed observations are unavailable") from exc
            raise

    @contextmanager
    def climate_request(self) -> Iterator["SnowflakeObservationRepository"]:
        with self.request_session() as repository:
            yield repository

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

    @property
    def _release_schema(self) -> str:
        return (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}"
        )

    def query_context(
        self, query: ObservationQuery, offset: int
    ) -> tuple[str, list[tuple[Any, ...]]]:
        statement, params = context_query_with_release(
            self.view, self._release_schema, query, offset
        )
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    statement, params, timeout=self.settings.public_query_timeout_seconds
                )
                return parse_context_batch_rows(list(cursor.fetchall()))
        except AtlasDataUnavailableError:
            raise
        except Exception as exc:
            logger.info(
                "context_observation_query_failed",
                extra={
                    "context": {
                        "failure_class": "dependency_error",
                        "outcome": "dependency_error",
                        "snowflake_error_type": type(exc).__name__,
                    }
                },
            )
            raise AtlasDataUnavailableError("Governed county context is unavailable") from exc

    def current_release(self) -> str:
        schema = (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}"
        )
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    f"SELECT RELEASE_ID FROM {schema}.CURRENT_RELEASE_V LIMIT 2",
                    timeout=self.settings.public_query_timeout_seconds,
                )
                rows = cursor.fetchmany(2)
            if (
                len(rows) != 1
                or len(rows[0]) != 1
                or not isinstance(rows[0][0], str)
                or not rows[0][0].strip()
            ):
                raise AtlasDataUnavailableError("No current governed release")
            return rows[0][0]
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
        from .cdc_surveillance import IDENTITIES

        if (
            isinstance(self.repository, SnowflakeObservationRepository)
            and self.cdc_adapter is not None
            and query.measure_id in IDENTITIES
        ):
            check()
            self.cdc_adapter.validate_query(query)
            with self.repository.request_session(close_on_exit_error=True) as repository:
                assert repository._connection is not None
                with self.cdc_adapter.request_session(repository._connection) as adapter:
                    return self._search(query, repository, check, cdc_adapter=adapter)
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
        self,
        query: ObservationQuery,
        repository: ObservationRepository,
        check: Callable[[], None],
        *,
        cdc_adapter: "CDCObservationAdapter | None" = None,
    ) -> ObservationCollection:
        check()
        from .cdc_surveillance import IDENTITIES

        adapter = cdc_adapter or self.cdc_adapter
        cdc = adapter is not None and query.measure_id in IDENTITIES
        if cdc:
            assert adapter is not None
            adapter.validate_query(query)
        if (
            not cdc
            and query.measure_id in CONTEXT_FIELDS
            and isinstance(repository, SnowflakeObservationRepository)
        ):
            return self._search_context_snowflake(query, repository, check)
        with phase("release_lookup"), cdc_phase("release_lookup"):
            release = repository.current_release()
        if not isinstance(release, str) or not release.strip():
            raise AtlasDataUnavailableError("No current governed release")
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
            assert adapter is not None
            items = [item for item in adapter.search(query, release, offset, checkpoint=check)]
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
        # Only a successful initial non-CDC query establishes an empty published
        # selection. CDC admission errors never reach this branch as empty data.
        unavailable = query.measure_id not in IDENTITIES and not items and offset == 0
        if unavailable:
            # A current-view pointer switch can make an old-release filter empty.
            # Revalidate before asserting a successful absence of published rows.
            check()
            if repository.current_release() != release:
                raise AtlasDataUnavailableError("Current observation release changed")
            check()
        meta = CollectionMeta(
            next_page_token=token,
            availability="UNAVAILABLE" if unavailable else "AVAILABLE",
            reason="NO_PUBLISHED_RESULTS" if unavailable else None,
            release_id=release,
            limitations=[
                "No published observations match this selection in the checked release. "
                "This does not establish biological absence, source coverage, "
                "or geography membership."
            ]
            if unavailable
            else [],
        )
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

    def _search_context_snowflake(
        self,
        query: ObservationQuery,
        repository: SnowflakeObservationRepository,
        check: Callable[[], None],
    ) -> ObservationCollection:
        check()
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
        token_release: str | None = None
        offset = 0
        if query.page_token is not None:
            if len(query.page_token) > 2048:
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.")
            try:
                payload = decode(query.page_token)
                token_release = payload["release"]
                offset = payload["offset"]
                if (
                    payload["query"] != fingerprint
                    or not isinstance(token_release, str)
                    or not token_release.strip()
                    or type(offset) is not int
                    or offset < 1
                    or offset > 10_000
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.") from exc
        check()
        with context_phase("release_lookup"), context_phase("observation_query"):
            release, rows = repository.query_context(query, offset)
        if token_release is not None and release != token_release:
            raise AtlasDataUnavailableError("Current observation release changed")
        check()
        more = len(rows) > query.page_size
        if not rows and offset == 0:
            with context_phase("measure_lookup"):
                if not repository.measure_exists(query.measure_id, release):
                    raise PublicQueryError(
                        "RESOURCE_NOT_FOUND", "Measure has no published county observations."
                    )
            raise PublicQueryError(
                "RESOURCE_NOT_FOUND", "No admitted county context for this selection."
            )
        with context_phase("mapping"):
            items = [context_observation(row) for row in rows]
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
        token = self._token(fingerprint, release, offset + query.page_size) if more else None
        meta = CollectionMeta(
            next_page_token=token,
            availability="AVAILABLE",
            reason=None,
            release_id=release,
            limitations=[],
        )
        return CollectionEnvelope[CountyContextObservation](
            data=items, meta=meta, links=CollectionLinks()
        )

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
