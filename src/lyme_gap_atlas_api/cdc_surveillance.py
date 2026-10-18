"""Narrow API binding to the immutable, admitted Data CDC producer."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from time import perf_counter
from typing import Any

from snowflake.connector import SnowflakeConnection

from .cdc_timing import phase, record_processing
from .config import ApiSettings
from .dependency_telemetry import connect
from .public_contract import (
    EvidenceReference,
    GeographyIdentity,
    GeographyType,
    Observation,
    ObservationQuery,
    PublicQueryError,
    ValueState,
)
from .repository import AtlasDataUnavailableError
from .surveillance_delivery import (
    BundleBinding,
    EvidenceDelivery,
    SourceIdentity,
    SupportedSlice,
    SurveillanceDelivery,
    _evidence_validator,
    resolve_context,
)

RELEASE = "governed-2026-10-15-january-climate-prod"
BUNDLE = "53e50aa37c701635e96c48cd4931c4c4a9e7e695c146263fb9bb335e541ae74d"
PERIOD = date(2025, 12, 31)
SOURCE = "cdc_arbonet_tick_module"
IDENTITIES = {
    "scapularis_status": (
        "cdc-ixodes-county-status-2025",
        "b81c116f-d93d-4c6b-9d11-ac2477e0e242",
        "metadata-revision:v1:3300365cfd0af87dcc80afdd94aa4b35f8651b2ab5ba898edb8a282f562016fd",
        "tick",
    ),
    "burgdorferi_status": (
        "cdc-ixodes-pathogen-status-2025",
        "92b22f19-0d5d-4576-a7c6-6c9af9343edf",
        "metadata-revision:v1:f864fe95d98836fcc030e5b2a15d3a63c2bec50011cde1de59318b34219188f0",
        "pathogen",
    ),
}
BINDING = BundleBinding(RELEASE, BUNDLE, "API #246 reviewed unchanged CDC carry-forward")
Producer = Callable[[str, str], dict[str, Any]]


def _read(measure: str, county: str, *, settings: ApiSettings | None = None) -> dict[str, Any]:
    runtime = settings or ApiSettings()
    _require(
        runtime.snowflake_auth_method == "key_pair" and runtime.snowflake_user == "OH_LYME_API_SVC"
    )
    try:
        with connect(runtime) as connection:
            _setup_session(connection, runtime)
            return _read_on_connection(measure, county, connection, runtime)
    except Exception:
        raise AtlasDataUnavailableError("Governed CDC surveillance is unavailable") from None


def _setup_session(connection: SnowflakeConnection, settings: ApiSettings) -> None:
    timeout = min(settings.public_query_timeout_seconds, 30)
    try:
        with phase("session_setup"), connection.cursor() as cursor:
            cursor.execute(
                f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS={timeout}", timeout=timeout
            )
            cursor.execute("USE SECONDARY ROLES NONE", timeout=timeout)
    except Exception:
        raise AtlasDataUnavailableError("Governed CDC surveillance is unavailable") from None


def _read_on_connection(
    measure: str, county: str, connection: SnowflakeConnection, settings: ApiSettings
) -> dict[str, Any]:
    from lyme_gap_atlas_data import cdc_consumer_runtime

    # Data retains identity and exact record admission on every call. Only the
    # exclusively owned session is reused; SQL, binds, fetch bound and pin stay fixed.
    timeout = min(settings.public_query_timeout_seconds, 30)
    try:
        with connection.cursor() as cursor:
            io_seconds = 0.0

            def query(sql: str, params: tuple[str, ...]) -> list[dict[str, Any]]:
                nonlocal io_seconds
                started = perf_counter()
                try:
                    with phase(
                        "identity"
                        if sql == cdc_consumer_runtime.IDENTITY_SQL
                        else "authority_query"
                    ):
                        cursor.execute(sql, params, timeout=timeout)
                        columns = [str(column[0]) for column in cursor.description or []]
                        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchmany(2)]
                finally:
                    io_seconds += perf_counter() - started

            started = perf_counter()
            try:
                with phase("producer"):
                    return cdc_consumer_runtime._project(
                        query,
                        cdc_consumer_runtime._resources(),
                        measure,
                        county,
                        "prod",
                        release_id=RELEASE,
                    )
            finally:
                record_processing(max(0.0, perf_counter() - started - io_seconds))
    except Exception:
        raise AtlasDataUnavailableError("Governed CDC surveillance is unavailable") from None


def _require(condition: bool) -> None:
    if not condition:
        raise AtlasDataUnavailableError("Governed CDC surveillance is unavailable")


class CDCResolver:
    """One immutable producer response, checked by the existing admission seam."""

    def __init__(self, measure: str, envelope: dict[str, Any]) -> None:
        self.measure = measure
        self.envelope = envelope

    def admitted_binding(self, release_id: str) -> BundleBinding:
        _require(release_id == RELEASE)
        return BINDING

    def supported_slice(self, release_id: str) -> SupportedSlice:
        self.admitted_binding(release_id)
        return SupportedSlice(
            self.measure,
            SOURCE,
            "CUMULATIVE",
            PERIOD,
            PERIOD,
            frozenset({ValueState.OBSERVED, ValueState.NO_RECORDS}),
            self.measure,
            "CUMULATIVE_THROUGH_DATE",
        )

    def resolve(self, observation: Observation) -> SurveillanceDelivery:
        semantic = self.envelope["semantic"]
        source = semantic["provenance"]
        item = semantic["observation"]
        identity = SourceIdentity(
            item["id"],
            item["revision_id"],
            source["source_id"]["value"],
            source["source_version_id"]["value"],
        )
        return SurveillanceDelivery(
            BINDING,
            observation.observation_id,
            identity,
            evidence=EvidenceDelivery(identity, self.envelope),
        )


class CDCObservationAdapter:
    def __init__(self, settings: ApiSettings, producer: Producer | None = None) -> None:
        self.settings = settings
        self._producer_override = producer
        self.producer = producer or (
            lambda measure, county: _read(measure, county, settings=settings)
        )

    @contextmanager
    def request_session(self, connection: SnowflakeConnection) -> Iterator["CDCObservationAdapter"]:
        """Borrow one request's session; never mutate the application adapter."""
        _setup_session(connection, self.settings)
        yield CDCObservationAdapter(
            self.settings,
            self._producer_override
            or (
                lambda measure, county: _read_on_connection(
                    measure, county, connection, self.settings
                )
            ),
        )

    def validate_query(self, query: ObservationQuery) -> None:
        """Reject unsupported requests before any repository or producer read."""
        _require(query.measure_id in IDENTITIES)
        # Cumulative evidence must not be represented as an annual sampling bucket.
        if query.year is not None or query.start_date != PERIOD or query.end_date != PERIOD:
            raise PublicQueryError(
                "UNSUPPORTED_FILTER",
                "CDC status is cumulative as published; omit year and use "
                "start_date=end_date=2025-12-31.",
            )
        _require(
            self.settings.snowflake_auth_method == "key_pair"
            and self.settings.snowflake_user == "OH_LYME_API_SVC"
            and self.settings.snowflake_role == "OH_LYME_PROD_READ"
            and self.settings.snowflake_database == "ONE_HEALTH_LYME_GAP_ATLAS_PROD"
            and self.settings.snowflake_warehouse == "COMPUTE_WH"
        )

    def search(
        self,
        query: ObservationQuery,
        release: str,
        offset: int,
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> list[Observation]:
        self.validate_query(query)
        _require(release == RELEASE)
        items = []
        # One page plus lookahead only; Data verifies each published record's
        # exact retained admission. Source coverage alone never authorizes it.
        for county in sorted(query.geography_id)[offset : offset + query.page_size + 1]:
            if checkpoint is not None:
                checkpoint()
            try:
                items.append(self._observation(query.measure_id, county))
            except AtlasDataUnavailableError:
                raise
            except Exception:
                # Never leak Data's private authority or database diagnostics.
                raise AtlasDataUnavailableError(
                    "Governed CDC surveillance is unavailable"
                ) from None
        return items

    def _observation(self, measure: str, county: str) -> Observation:
        envelope = self.producer(measure, county)
        with phase("mapping"):
            return self._map_observation(measure, county, envelope)

    def _map_observation(self, measure: str, county: str, envelope: dict[str, Any]) -> Observation:
        _require(_evidence_validator.is_valid(envelope))
        semantic = envelope["semantic"]
        item, metadata, lineage = (semantic[k] for k in ("observation", "measure", "lineage"))
        dataset, version, revision, public_source_key = IDENTITIES[measure]
        _require(metadata["metadata_revision_id"] == lineage["metadata_revision_id"] == revision)
        _require(metadata["metadata_revision"] == 3 and metadata["semantic_version"] == "1.0.0")
        _require(lineage["dataset_id"] == dataset and lineage["source_version_id"] == version)
        _require(
            semantic["provenance"]["source_version_id"] == {"state": "KNOWN", "value": version}
        )
        _require(item["geography"]["county_fips"] == county and item["strata"] == {})
        _require(
            envelope["surveillance_evidence"].get("normalized_state")
            in {"detected", "unknown", "established"}
        )
        freshness = semantic["freshness"]
        acquired = freshness["retrieved_at"]["value"]
        limitations = [x["text"] for x in metadata["limitations"]]
        # Project the existing DTO directly: the legacy SQL tuple mapper requires
        # a methodology catalog ID that this admitted producer does not supply.
        # Never manufacture catalog identity/labels from a measure or a version.
        observation = Observation(
            observation_id=item["id"],
            measure_id=measure,
            geography=GeographyIdentity(geography_type=GeographyType.county, geography_id=county),
            period_start=PERIOD,
            period_end=PERIOD,
            temporal_grain="CUMULATIVE",
            value=item["value"],
            value_state=item["value_state"],
            unit=metadata["unit"],
            denominator=metadata["denominator"],
            semantic_version=metadata["semantic_version"],
            release_id=RELEASE,
            source_id=public_source_key,
            lineage_source_id=SOURCE,
            dataset_id=dataset,
            source_label=None,
            source_vintage=freshness["source_vintage"]["value"],
            source_url=None,
            atlas_acquired_at=acquired,
            methodology_id=None,
            methodology=None,
            methodology_version=lineage["transformation_version"],
            release_methodology_version=lineage["methodology_version"],
            metadata_revision_id=revision,
            limitations=limitations,
            provenance_ref=item["id"],
            evidence=EvidenceReference(
                resource_type="observation",
                resource_id=item["id"],
                source_id=public_source_key,
                provenance_ref=item["id"],
                semantic_version=metadata["semantic_version"],
                methodology_version=lineage["transformation_version"],
                release_id=RELEASE,
            ),
        )
        observation.surveillance_context = resolve_context(
            observation, CDCResolver(measure, envelope)
        )
        return observation
