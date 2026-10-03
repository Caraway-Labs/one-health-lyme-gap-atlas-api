"""Bounded first-party reads of the governed DATA intelligence projection."""

import hashlib
import json
from datetime import date
from typing import Any, Literal, Protocol, cast

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field

from .briefings import BriefingModel, BriefingSource, IntelligenceItem
from .config import ApiSettings
from .dependency_telemetry import connect
from .intelligence_projection import IntelligenceItemProjectionV2
from .public_contract import PublicQueryError
from .public_routes import PROBLEMS
from .public_tokens import decode, encode
from .repository import AtlasDataUnavailableError, _sql_identifier

ITEM_COLUMNS = (
    "item_id",
    "revision_id",
    "source_id",
    "registry_version",
    "transport",
    "deduplication_key",
    "content_sha256",
    "transport_identity_sha256",
    "canonical_url",
    "title",
    "published_at",
    "updated_at",
    "event_at",
    "fetched_at",
    "excerpt",
    "field_states",
    "geographies",
    "topics",
    "provenance",
    "limitations",
    "contract_version",
    "content_is_untrusted",
)
VARIANTS = {"field_states", "geographies", "topics", "provenance", "limitations"}
V2_COLUMNS = ITEM_COLUMNS + ("publisher_metadata", "derived_metadata")
UNAVAILABLE = "Governed intelligence data is unavailable."


class FeedEvidence(BriefingModel):
    item: IntelligenceItem | IntelligenceItemProjectionV2
    source: BriefingSource


class FeedSource(BriefingSource):
    operational_state: Literal["unavailable"] = "unavailable"
    diagnostic: Literal["SOURCE_HEALTH_NOT_PUBLISHED"] = "SOURCE_HEALTH_NOT_PUBLISHED"


class FeedMeta(BriefingModel):
    next_page_token: str | None = Field(description="Opaque filter/state-bound continuation.")
    projection_version: Literal["intelligence-feed-read-v2"] = "intelligence-feed-read-v2"
    source_health_available: Literal[False] = False
    limitations: tuple[str, ...] = (
        "Publication intelligence, not disease occurrence or a public-health alert.",
        "Source health is not published; item fetch timestamps are not health evidence.",
    )


class FeedItems(BriefingModel):
    data: tuple[FeedEvidence, ...]
    meta: FeedMeta


class FeedSources(BriefingModel):
    data: tuple[FeedSource, ...]
    meta: FeedMeta
    source_catalog_complete: Literal[False] = False


class FeedRepository(Protocol):
    def read(
        self,
        kind: str,
        source_id: str | None,
        start: date | None,
        end: date | None,
        offset: int,
        size: int,
    ) -> tuple[str, list[tuple[Any, ...]]]: ...


class SnowflakeFeedRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    def read(
        self,
        kind: str,
        source_id: str | None,
        start: date | None,
        end: date | None,
        offset: int,
        size: int,
    ) -> tuple[str, list[tuple[Any, ...]]]:
        if self.settings.presentation_database not in {
            "ONE_HEALTH_LYME_GAP_ATLAS_DEV",
            "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        } or kind not in {"items", "sources"}:
            raise AtlasDataUnavailableError(UNAVAILABLE)
        view_name = (
            "INTELLIGENCE_FEED_V2"
            if self.settings.intelligence_feed_projection_version == "v2"
            else "INTELLIGENCE_FEED_V"
        )
        view = (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}.{view_name}"
        )
        clauses, params = [], []
        if source_id is not None:
            clauses.append("SOURCE_ID = %s")
            params.append(source_id)
        if start is not None:
            clauses.append("SUBSTR(PUBLISHED_AT, 1, 10) >= %s")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("SUBSTR(PUBLISHED_AT, 1, 10) <= %s")
            params.append(end.isoformat())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        count = "COUNT(*)" if kind == "items" else "COUNT(DISTINCT SOURCE_ID, REGISTRY_VERSION)"
        state_sql = (
            f"SELECT {count}, HASH_AGG(ITEM_ID, REVISION_ID, SOURCE_ID, REGISTRY_VERSION, "
            f"FETCHED_AT, CONTENT_SHA256, ORGANIZATION, REVIEWED_TRUST_CLASSIFICATION) "
            f"FROM {view}{where}"
        )
        item_columns = (
            V2_COLUMNS
            if self.settings.intelligence_feed_projection_version == "v2"
            else ITEM_COLUMNS
        )
        columns = ", ".join(name.upper() for name in item_columns)
        columns += ", ORGANIZATION, REVIEWED_TRUST_CLASSIFICATION"
        if kind == "sources":
            columns = (
                "DISTINCT SOURCE_ID, REGISTRY_VERSION, ORGANIZATION, REVIEWED_TRUST_CLASSIFICATION"
            )
        order = (
            "ITEM_ID, REVISION_ID, SOURCE_ID, REGISTRY_VERSION"
            if kind == "items"
            else "SOURCE_ID, REGISTRY_VERSION"
        )
        statement = f"SELECT {columns} FROM {view}{where} ORDER BY {order} LIMIT %s OFFSET %s"
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    state_sql, tuple(params), timeout=self.settings.public_query_timeout_seconds
                )
                before = cursor.fetchone()
                if before is None or before[0] > self.settings.public_query_result_ceiling:
                    raise PublicQueryError(
                        "QUERY_TOO_BROAD", "Narrow the source or publication dates."
                    )
                cursor.execute(
                    statement,
                    (*params, size + 1, offset),
                    timeout=self.settings.public_query_timeout_seconds,
                )
                rows = cursor.fetchall()
                cursor.execute(
                    state_sql, tuple(params), timeout=self.settings.public_query_timeout_seconds
                )
                if cursor.fetchone() != before:
                    raise AtlasDataUnavailableError(UNAVAILABLE)
            marker = hashlib.sha256(json.dumps(before, default=str).encode()).hexdigest()
            return marker, rows
        except PublicQueryError:
            raise
        except Exception:
            raise AtlasDataUnavailableError(UNAVAILABLE) from None


class FeedService:
    def __init__(self, repository: FeedRepository) -> None:
        self.repository = repository

    def search(
        self,
        kind: str,
        source_id: str | None,
        start: date | None,
        end: date | None,
        size: int,
        token: str | None,
    ) -> FeedItems | FeedSources:
        if start and end and start > end:
            raise PublicQueryError("INVALID_REQUEST", "Publication date range is reversed.")
        fingerprint = hashlib.sha256(
            json.dumps([kind, source_id, str(start), str(end), size]).encode()
        ).hexdigest()
        offset, prior = 0, None
        if token:
            try:
                payload = decode(token)
                offset, prior = payload["offset"], payload["state"]
                if (
                    payload["query"] != fingerprint
                    or type(offset) is not int
                    or not 0 < offset <= 10000
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.") from None
        marker, rows = self.repository.read(kind, source_id, start, end, offset, size)
        if prior is not None and prior != marker:
            raise PublicQueryError("INVALID_REQUEST", "Projection changed; restart pagination.")
        next_token = (
            encode({"query": fingerprint, "state": marker, "offset": offset + size})
            if len(rows) > size
            else None
        )
        meta = FeedMeta(next_page_token=next_token)
        try:
            if kind == "sources":
                sources = tuple(
                    FeedSource.model_validate(
                        dict(
                            zip(
                                (
                                    "source_id",
                                    "registry_version",
                                    "organization",
                                    "reviewed_trust_classification",
                                ),
                                row,
                                strict=True,
                            )
                        )
                    )
                    for row in rows[:size]
                )
                return FeedSources(data=sources, meta=meta)
            evidence = []
            for row in rows[:size]:
                if len(row) == len(V2_COLUMNS) + 2:
                    document = dict(zip(V2_COLUMNS, row[:-2], strict=True))
                    fields = VARIANTS | {"publisher_metadata", "derived_metadata"}
                else:
                    document = dict(zip(ITEM_COLUMNS, row[:-2], strict=True))
                    fields = VARIANTS
                for field in fields:
                    if isinstance(document[field], str):
                        document[field] = json.loads(document[field])
                item = (
                    IntelligenceItemProjectionV2.model_validate(document)
                    if document["contract_version"] == "2.0.0"
                    else IntelligenceItem.model_validate(document)
                )
                source = BriefingSource(
                    source_id=item.source_id,
                    registry_version=item.registry_version,
                    organization=row[-2],
                    reviewed_trust_classification=row[-1],
                )
                evidence.append(FeedEvidence(item=item, source=source))
            return FeedItems(data=tuple(evidence), meta=meta)
        except Exception:
            raise AtlasDataUnavailableError(UNAVAILABLE) from None


router = APIRouter(tags=["Intelligence feed"])


def _service(request: Request, response: Response, page_size: int) -> FeedService:
    settings = cast(ApiSettings, request.app.state.public_settings)
    response.headers["Cache-Control"] = "no-store"
    allowed = {"page_size", "page_token"}
    if request.url.path.endswith("/items"):
        allowed.update({"source_id", "published_from", "published_to"})
    if set(request.query_params) - allowed:
        raise PublicQueryError("INVALID_REQUEST", "Unknown query parameter.")
    if not settings.intelligence_feed_read_enabled:
        raise HTTPException(503, "Governed intelligence reading is not enabled.")
    if page_size > settings.public_page_size_max:
        raise PublicQueryError("INVALID_REQUEST", "page_size exceeds the configured maximum.")
    return cast(FeedService, request.app.state.intelligence_feed_service)


@router.get(
    "/v1/intelligence/items",
    response_model=FeedItems,
    operation_id="listIntelligenceItems",
    summary="Read normalized publication intelligence",
    responses=PROBLEMS,
    description="First-party read only; no ingestion. Stable identity ordering, source/revision "
    "provenance and separate publisher/fetch dates. Optional inclusive publication dates exclude "
    "unknown publisher dates. No retention cutoff is applied. page_size 1–500; "
    "opaque state/filter-bound tokens may expire on restart or ingestion. no-store; "
    "disabled/unavailable is 503, not empty data.",
)
def items(
    request: Request,
    response: Response,
    source_id: str | None = Query(None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}$"),
    published_from: date | None = None,
    published_to: date | None = None,
    page_size: int = Query(100, ge=1, le=500),
    page_token: str | None = Query(None, max_length=2048),
) -> FeedItems:
    return cast(
        FeedItems,
        _service(request, response, page_size).search(
            "items",
            source_id,
            published_from,
            published_to,
            page_size,
            page_token,
        ),
    )


@router.get(
    "/v1/intelligence/sources",
    response_model=FeedSources,
    operation_id="listIntelligenceSources",
    summary="Read source identities represented by published intelligence",
    responses=PROBLEMS,
    description="First-party read only. Source catalog covers represented item sources, "
    "not the private "
    "registry or all active sources. Health is explicitly unavailable until DATA publishes it. "
    "No freshness thresholds are inferred. page_size 1–500, state-bound tokens, "
    "no-store. No ingestion.",
)
def sources(
    request: Request,
    response: Response,
    page_size: int = Query(100, ge=1, le=500),
    page_token: str | None = Query(None, max_length=2048),
) -> FeedSources:
    return cast(
        FeedSources,
        _service(request, response, page_size).search(
            "sources",
            None,
            None,
            None,
            page_size,
            page_token,
        ),
    )
