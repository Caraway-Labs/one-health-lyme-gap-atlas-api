"""Bounded first-party reads of the governed DATA intelligence projection."""

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Any, Final, Literal, Protocol, Self, cast

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, Field, ValidationError, model_validator

from .briefings import (
    BriefingModel,
    BriefingSource,
    Digest,
    IntelligenceItem,
    State,
    Text,
    Utc,
    public_canonical_url,
)
from .config import ApiSettings
from .intelligence_projection import IntelligenceItemProjectionV2, ProjectionProvenance
from .models import ProblemDetails
from .public_contract import PublicQueryError
from .public_docs import DESCRIPTION_422, problem_response, problem_type_uri
from .public_routes import PROBLEMS
from .public_tokens import decode, encode
from .repository import AtlasDataUnavailableError, _sql_identifier
from .snowflake_read_session import read_connection
from .telemetry_logging import operational_logger

logger = operational_logger(__name__)

_UNSAFE_TEXT = re.compile(r"[<>\x00-\x1f\x7f]")
_PMID = re.compile(r"^[1-9][0-9]{0,9}$")
_PLAIN = r"^[^<>\x00-\x1f\x7f]*$"
_SOURCE_FAMILIES = frozenset(
    {"cdc_mmwr", "cdc_eid", "cdc_other", "nih_niaid", "pubmed", "state_local", "other"}
)
_CDC_FAMILIES = frozenset({"cdc_mmwr", "cdc_eid", "cdc_other"})
_MMWR_SERIES_LABELS = frozenset({"mmwr", "morbidity & mortality weekly report"})
_CDC_ATTRIBUTION = "Source: Centers for Disease Control and Prevention"
_NIH_ATTRIBUTION = "Source: National Institutes of Health"
_PUBMED_ATTRIBUTION = "Courtesy of the U.S. National Library of Medicine"
_DESCRIPTION_WITHHELD = {"text": None, "state": "withheld"}
_DESCRIPTION_NOT_PROVIDED = {"text": None, "state": "not_provided"}
_ABSTRACT_WITHHELD = {"text": None, "state": "withheld", "copyright_line": None}
_ABSTRACT_SUPPRESSED = {"text": None, "state": "suppressed", "copyright_line": None}
# The governed view does not project this column yet. Select it only when it exists.
_ABSTRACT_SUPPRESSED_COLUMN = "ABSTRACT_SUPPRESSED"
_FEED_SUMMARY = (
    "The feed returns publisher summaries and, for PubMed, abstracts with their copyright line. "
    "It returns no article bodies or media."
)

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
INTELLIGENCE_SOURCE_HEALTH_VIEW = "INTELLIGENCE_SOURCE_HEALTH_V1"
SOURCE_HEALTH_CACHE_TTL_SECONDS = 300
SOURCE_HEALTH_NEGATIVE_CACHE_TTL_SECONDS = 60
ABSTRACT_SUPPRESSED_COLUMN_CACHE_TTL_SECONDS = 300
ABSTRACT_SUPPRESSED_COLUMN_NEGATIVE_CACHE_TTL_SECONDS = 60
# Present publisher dates first, newest parsed time first, other date states last.
FEED_LIST_ORDER = (
    "IFF(FIELD_STATES:published_at::VARCHAR = 'present', 0, 1), "
    "TRY_TO_TIMESTAMP_TZ(PUBLISHED_AT) DESC NULLS LAST, "
    "ITEM_ID, REVISION_ID, SOURCE_ID, REGISTRY_VERSION"
)
# data#815 column names. The view is not in PROD yet; this mapping is the only
# place those names are spelled. last_status and items_current are not selected.
INTELLIGENCE_SOURCE_HEALTH_COLUMNS: Final[Mapping[str, str]] = {
    "source_id": "SOURCE_ID",
    "last_completed_at": "LAST_COMPLETED_AT",
    "last_status": "LAST_STATUS",
    "last_error_code": "LAST_ERROR_CODE",
    "items_current": "ITEMS_CURRENT",
    "state": "STATE",
}
_SOURCE_HEALTH_RESPONSE_FIELDS: Final[tuple[str, ...]] = (
    "source_id",
    "last_completed_at",
    "last_error_code",
    "state",
)
# data#802 allowlisted codes. Anything else is free text and is omitted.
SOURCE_HEALTH_ERROR_CODE_PATTERN = (
    r"^(?:INTELLIGENCE_[A-Z0-9_]{3,80}|FEED_[A-Z0-9_]{1,64}|"
    r"MALFORMED_FEED|UNCLASSIFIED_[A-Za-z][A-Za-z0-9]{0,80})$"
)
_SOURCE_HEALTH_ERROR_CODE = re.compile(SOURCE_HEALTH_ERROR_CODE_PATTERN)
_SOURCE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}$")
_PRESENTATION_DATABASES = {
    "ONE_HEALTH_LYME_GAP_ATLAS_DEV",
    "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
}
_HEALTH_STATE_RANK = {"failing": 3, "stale": 2, "healthy": 1}

FEED_PROBLEMS = {
    **PROBLEMS,
    422: {
        **problem_response(
            422,
            None,
            "One or more request values are invalid.",
            type_name="validation",
            title="Invalid request",
            description=DESCRIPTION_422,
        ),
    },
}


_FEED_RAW_PREFIX = (
    "item_id",
    "revision_id",
    "title",
    "canonical_url",
    "published_at",
    "fetched_at",
    "provenance",
    "limitations",
    "contract_version",
    "publication_date_state",
    "publisher",
)
_SOURCE_FIELDS = (
    "source_id",
    "registry_version",
    "organization",
    "reviewed_trust_classification",
)
FEED_COLUMNS = (
    *_FEED_RAW_PREFIX,
    "source_family",
    "description",
    "abstract",
    "tags",
    "authors",
    "pmid",
    "attribution",
)
_FEED_PUBLISHER_OBJECT = (
    "OBJECT_CONSTRUCT_KEEP_NULL("
    "'source_family', PUBLISHER_METADATA:source_family, "
    "'source_item_id', PUBLISHER_METADATA:source_item_id, "
    "'description', PUBLISHER_METADATA:description, "
    "'abstract', PUBLISHER_METADATA:abstract, "
    "'categories', PUBLISHER_METADATA:categories, "
    "'authors', PUBLISHER_METADATA:authors, "
    "'abstract_suppressed', PUBLISHER_METADATA:abstract_suppressed"
    ") AS PUBLISHER_FEED"
)


def _plain(value: object, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit and _UNSAFE_TEXT.search(value) is None


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, dict):
        return dict(value)
    return None


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, list):
        return value
    return []


def _suppressed_flag(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _mapping_feed(value: Any) -> dict[str, Any]:
    parsed = _as_mapping(value)
    return {} if parsed is None else parsed


def _pack_feed_row(row: tuple[Any, ...], suppressed_column: bool) -> tuple[Any, ...]:
    if suppressed_column:
        feed = _mapping_feed(row[11])
        if _suppressed_flag(row[12]):
            feed = {**feed, "abstract_suppressed": True}
        return (*row[:11], feed, *row[13:17])
    return (*row[:11], _mapping_feed(row[11]), *row[12:16])


def feed_attribution(family: str, publisher: str | None) -> str:
    """Credit line for one source family. The schema bound is 200 characters."""
    if family in _CDC_FAMILIES:
        text = _CDC_ATTRIBUTION
    elif family == "nih_niaid":
        text = _NIH_ATTRIBUTION
    elif family == "pubmed":
        text = _PUBMED_ATTRIBUTION
    else:
        text = f"Source: {publisher or ''}"
    return text[:200]


def _labels(value: Any, *, limit: int, length: int, drop_mmwr: bool) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        if not _plain(item, length) or item == "":
            continue
        assert isinstance(item, str)
        key = item.casefold()
        if (drop_mmwr and key in _MMWR_SERIES_LABELS) or key in seen:
            continue
        seen.add(key)
        chosen.append(item)
        if len(chosen) == limit:
            break
    return chosen


def _authors(value: Any) -> list[str]:
    chosen: list[str] = []
    for item in _as_list(value):
        if not _plain(item, 300) or item == "":
            continue
        assert isinstance(item, str)
        chosen.append(item)
        if len(chosen) == 20:
            break
    return chosen


def _project_description(raw: Any, *, pubmed: bool) -> tuple[dict[str, Any], int]:
    # PubMed has no publisher summary. The abstract is a separate field.
    if pubmed:
        return dict(_DESCRIPTION_NOT_PROVIDED), 0
    obj = _as_mapping(raw)
    if obj is None:
        return dict(_DESCRIPTION_WITHHELD), 0
    if "format" in obj and obj.get("format") not in {None, "plain_text"}:
        return dict(_DESCRIPTION_WITHHELD), 1
    if "origin" in obj and obj.get("origin") not in {None, "publisher"}:
        return dict(_DESCRIPTION_WITHHELD), 1
    state = obj.get("state")
    text = obj.get("text")
    if state == "withheld":
        return dict(_DESCRIPTION_WITHHELD), 0
    if state == "not_provided" and text is None:
        return dict(_DESCRIPTION_NOT_PROVIDED), 0
    if state == "empty" and text == "":
        return {"text": "", "state": "empty"}, 0
    if state == "present" and _plain(text, 4096) and text != "":
        return {"text": text, "state": "present"}, 0
    return dict(_DESCRIPTION_WITHHELD), 1


def _copyright_line(value: Any) -> tuple[str | None, int]:
    if value is None or value == "":
        return None, 0
    if _plain(value, 1000):
        assert isinstance(value, str)
        return value, 0
    return None, 1


def _project_abstract(
    raw: Any, *, pubmed: bool, suppressed: bool
) -> tuple[dict[str, Any] | None, int]:
    if not pubmed:
        return None, 0
    if suppressed:
        return dict(_ABSTRACT_SUPPRESSED), 0
    obj = _as_mapping(raw)
    if obj is None:
        return dict(_ABSTRACT_WITHHELD), 0
    if obj.get("state") == "suppressed" or _suppressed_flag(obj.get("suppressed")):
        return dict(_ABSTRACT_SUPPRESSED), 0
    state = obj.get("state")
    text = obj.get("text")
    copyright_line, copyright_failures = _copyright_line(obj.get("copyright_line"))
    if state == "withheld":
        return dict(_ABSTRACT_WITHHELD), 0
    if state == "not_provided" and text is None:
        return {"text": None, "state": "not_provided", "copyright_line": None}, 0
    if state == "empty" and text == "":
        return {"text": "", "state": "empty", "copyright_line": None}, 0
    if state == "present" and _plain(text, 6000) and text != "":
        return {
            "text": text,
            "state": "present",
            "copyright_line": copyright_line,
        }, copyright_failures
    return dict(_ABSTRACT_WITHHELD), 1 + copyright_failures


def project_feed_document(
    prefix: dict[str, Any], publisher_feed: Any, source: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Map one governed row onto a FeedEntry document.

    Invalid summary or abstract text becomes withheld for this entry. The count
    is the number of those texts rejected. The text itself is not returned.
    """
    raw = _as_mapping(publisher_feed)
    family = None if raw is None else raw.get("source_family")
    if not isinstance(family, str) or family not in _SOURCE_FAMILIES:
        raise ValueError("INTELLIGENCE_SOURCE_FAMILY_INVALID")
    suppressed = _suppressed_flag(raw.get("abstract_suppressed") if raw else None)
    description, description_failures = _project_description(
        None if raw is None else raw.get("description"), pubmed=family == "pubmed"
    )
    abstract, abstract_failures = _project_abstract(
        None if raw is None else raw.get("abstract"),
        pubmed=family == "pubmed",
        suppressed=suppressed,
    )
    pmid = None
    if family == "pubmed" and raw is not None:
        source_item_id = raw.get("source_item_id")
        if isinstance(source_item_id, str) and _PMID.fullmatch(source_item_id):
            pmid = source_item_id
    publisher = prefix.get("publisher")
    document = {
        **prefix,
        "source": source,
        "source_family": family,
        "description": description,
        "abstract": abstract,
        "tags": _labels(
            None if raw is None else raw.get("categories"),
            limit=100,
            length=200,
            drop_mmwr=True,
        ),
        "authors": _authors(None if raw is None else raw.get("authors")),
        "pmid": pmid,
        "attribution": feed_attribution(family, publisher if isinstance(publisher, str) else None),
    }
    return document, description_failures + abstract_failures


class FeedDescription(BriefingModel):
    """Publisher summary for one feed entry."""

    text: (
        Annotated[
            str,
            Field(
                max_length=4096,
                pattern=_PLAIN,
                description=(
                    "Plain text of the publisher summary. Non-empty when state is present, "
                    "an empty string when state is empty, and null otherwise."
                ),
            ),
        ]
        | None
    )
    state: Literal["present", "empty", "not_provided", "withheld"] = Field(
        description=(
            "present: text is non-empty. empty: the summary element is blank and text is "
            "an empty string. not_provided: the record has no summary and text is null. "
            "withheld: text is null."
        )
    )

    @model_validator(mode="after")
    def description_state(self) -> Self:
        if (
            (self.state == "present" and not self.text)
            or (self.state == "empty" and self.text != "")
            or (self.state in {"not_provided", "withheld"} and self.text is not None)
        ):
            raise ValueError("INTELLIGENCE_DESCRIPTION_STATE_INVALID")
        return self


class FeedAbstract(BriefingModel):
    """PubMed abstract for one feed entry."""

    text: (
        Annotated[
            str,
            Field(
                max_length=6000,
                pattern=_PLAIN,
                description=(
                    "Plain text of the PubMed abstract. Non-empty when state is present, "
                    "an empty string when state is empty, and null otherwise. The text is "
                    "complete."
                ),
            ),
        ]
        | None
    )
    state: Literal["present", "empty", "not_provided", "withheld", "suppressed"] = Field(
        description=(
            "present: text is non-empty. empty: the abstract element is blank and text is "
            "an empty string. not_provided: the record has no abstract and text is null. "
            "withheld: text is null. suppressed: text is null and the abstract is not shown."
        )
    )
    copyright_line: (
        Annotated[
            str,
            Field(
                max_length=1000,
                pattern=_PLAIN,
                description=(
                    "Copyright line from the record. Present only when state is present and "
                    "the record has one; otherwise null."
                ),
            ),
        ]
        | None
    )

    @model_validator(mode="after")
    def abstract_state(self) -> Self:
        if self.state == "present":
            if not self.text:
                raise ValueError("INTELLIGENCE_ABSTRACT_STATE_INVALID")
            return self
        if self.state == "empty":
            if self.text != "" or self.copyright_line is not None:
                raise ValueError("INTELLIGENCE_ABSTRACT_STATE_INVALID")
            return self
        if self.text is not None or self.copyright_line is not None:
            raise ValueError("INTELLIGENCE_ABSTRACT_STATE_INVALID")
        return self


class FeedEntry(BriefingModel):
    """One source-attributed publication in the intelligence feed."""

    item_id: Digest
    revision_id: Digest
    title: Text | None
    canonical_url: (
        Annotated[str, Field(max_length=4096), AfterValidator(public_canonical_url)] | None
    )
    published_at: Utc | None
    fetched_at: Utc
    provenance: ProjectionProvenance
    limitations: Annotated[tuple[Text, ...], Field(max_length=100)]
    contract_version: Literal["2.0.0"]
    publication_date_state: State
    publisher: Text | None
    source: BriefingSource
    source_family: Literal[
        "cdc_mmwr", "cdc_eid", "cdc_other", "nih_niaid", "pubmed", "state_local", "other"
    ] = Field(
        description=(
            "Publisher family. One of cdc_mmwr, cdc_eid, cdc_other, nih_niaid, pubmed, "
            "state_local, or other."
        )
    )
    description: FeedDescription = Field(description="Publisher summary for this entry.")
    abstract: FeedAbstract | None = Field(
        description=(
            "PubMed abstract for this entry, including its copyright line. "
            "Null when the entry is not PubMed."
        )
    )
    tags: Annotated[
        tuple[Annotated[str, Field(max_length=200, pattern=_PLAIN)], ...],
        Field(
            max_length=100,
            description=(
                "Publisher category labels in publisher order. PubMed entries use author "
                "keywords. The MMWR series labels MMWR and Morbidity & Mortality Weekly "
                "Report are omitted. Duplicate labels that differ only by case are omitted. "
                "Empty when the entry has none."
            ),
        ),
    ]
    authors: Annotated[
        tuple[Annotated[str, Field(max_length=300, pattern=_PLAIN)], ...],
        Field(
            max_length=20,
            description=("Structured author names in record order. Empty when the entry has none."),
        ),
    ]
    pmid: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,9}$")] | None = Field(
        description="PubMed identifier. Null when the entry is not a PubMed record."
    )
    attribution: Annotated[str, Field(max_length=200, pattern=_PLAIN)] = Field(
        description="Credit line shown with the entry."
    )

    @model_validator(mode="after")
    def date_state(self) -> Self:
        if (self.published_at is not None) != (self.publication_date_state == "present"):
            raise ValueError("INTELLIGENCE_PUBLICATION_DATE_STATE_INVALID")
        if self.attribution != feed_attribution(self.source_family, self.publisher):
            raise ValueError("INTELLIGENCE_ATTRIBUTION_INVALID")
        if self.source_family == "pubmed":
            if (
                self.abstract is None
                or self.description.text is not None
                or self.description.state != "not_provided"
            ):
                raise ValueError("INTELLIGENCE_PUBMED_FIELDS_INVALID")
        elif self.abstract is not None or self.pmid is not None:
            raise ValueError("INTELLIGENCE_PUBMED_FIELDS_INVALID")
        return self


class FeedEvidence(BriefingModel):
    item: IntelligenceItem | IntelligenceItemProjectionV2
    source: BriefingSource


class FeedSource(BriefingSource):
    operational_state: Literal["healthy", "stale", "failing", "unavailable"] = Field(
        default="unavailable",
        description=(
            "healthy, stale, or failing when the source-health projection has a row for "
            "this source. unavailable when it does not, or when that projection cannot be read."
        ),
    )
    diagnostic: Literal["SOURCE_HEALTH_NOT_PUBLISHED"] | None = Field(
        default="SOURCE_HEALTH_NOT_PUBLISHED",
        description=(
            "SOURCE_HEALTH_NOT_PUBLISHED when operational_state is unavailable. "
            "Null when a health row supplies the operational state."
        ),
    )
    last_completed_at: Utc | None = Field(
        default=None,
        description=(
            "UTC completion time from the source-health projection. "
            "Null when unavailable or absent on that row."
        ),
    )
    last_error_code: Annotated[str, Field(pattern=SOURCE_HEALTH_ERROR_CODE_PATTERN)] | None = Field(
        default=None,
        description=(
            "Allowlisted error code from the source-health projection. "
            "Free text and run identifiers are omitted."
        ),
    )

    @model_validator(mode="after")
    def health_diagnostic(self) -> Self:
        published = self.operational_state != "unavailable"
        if published == (self.diagnostic == "SOURCE_HEALTH_NOT_PUBLISHED"):
            raise ValueError("SOURCE_HEALTH_DIAGNOSTIC_MISMATCH")
        if self.operational_state == "unavailable" and (
            self.last_completed_at is not None or self.last_error_code is not None
        ):
            raise ValueError("SOURCE_HEALTH_UNAVAILABLE_HAS_EVIDENCE")
        return self


class FeedMeta(BriefingModel):
    next_page_token: str | None = Field(description="Opaque filter/state-bound continuation.")
    projection_version: Literal["intelligence-feed-read-v2"] = "intelligence-feed-read-v2"
    source_health_available: Literal[False] = False
    limitations: tuple[str, ...] = (
        "Publication intelligence, not disease occurrence or a public-health alert.",
        "Source health is not published; item fetch timestamps are not health evidence.",
    )


class SourceCatalogMeta(BriefingModel):
    next_page_token: str | None = Field(description="Opaque filter/state-bound continuation.")
    projection_version: Literal["intelligence-feed-read-v2"] = "intelligence-feed-read-v2"
    source_health_available: bool = Field(
        default=False,
        description=(
            "True when this response applied a source-health snapshot. False when that "
            "snapshot could not be read; every source is then unavailable."
        ),
    )
    limitations: tuple[str, ...] = (
        "Publication intelligence, not disease occurrence or a public-health alert.",
        (
            "Operational state is healthy, stale, or failing when the source-health projection "
            "has a row for that source. Otherwise it is unavailable with diagnostic "
            "SOURCE_HEALTH_NOT_PUBLISHED. Fetch timestamps are not operational health."
        ),
    )


class FeedItems(BriefingModel):
    data: tuple[FeedEvidence, ...]
    meta: FeedMeta


class MetadataFeed(BriefingModel):
    data: tuple[FeedEntry, ...]
    meta: FeedMeta


class FeedSources(BriefingModel):
    data: tuple[FeedSource, ...]
    meta: SourceCatalogMeta
    source_catalog_complete: Literal[False] = False


class FeedEntryMeta(BriefingModel):
    """Projection identity for one feed entry."""

    projection_version: Literal["intelligence-feed-read-v2"] = "intelligence-feed-read-v2"


class FeedEntryRead(BriefingModel):
    """One current intelligence feed entry."""

    data: FeedEntry
    meta: FeedEntryMeta = Field(default_factory=FeedEntryMeta)


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

    def read_item(self, item_id: str) -> list[tuple[Any, ...]]: ...


class SourceHealthResultTruncated(RuntimeError):
    """The health read returned more rows than the public result ceiling."""


@dataclass(frozen=True)
class SourceHealth:
    operational_state: Literal["healthy", "stale", "failing"]
    last_completed_at: str | None
    last_error_code: str | None


@dataclass(frozen=True)
class _HealthCache:
    expires_at: float
    value: dict[str, SourceHealth] | None


@dataclass(frozen=True)
class _ColumnPresenceCache:
    expires_at: float
    present: bool


class _UtcValue(BriefingModel):
    value: Utc


def _operational_state(value: object) -> Literal["healthy", "stale", "failing"] | None:
    if not isinstance(value, str):
        return None
    state = value.strip().casefold()
    if state == "healthy":
        return "healthy"
    if state == "stale":
        return "stale"
    if state == "failing":
        return "failing"
    return None


def _allowlisted_error_code(value: object) -> str | None:
    if isinstance(value, str) and _SOURCE_HEALTH_ERROR_CODE.fullmatch(value):
        return value
    return None


def _render_utc(value: object) -> str | None:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        elif " " in text and "T" not in text:
            text = text.replace(" ", "T", 1)
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    if moment.microsecond:
        digits = f"{moment.microsecond:06d}".rstrip("0")
        rendered = moment.strftime("%Y-%m-%dT%H:%M:%S.") + digits + "Z"
    else:
        rendered = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        return _UtcValue(value=rendered).value
    except ValidationError:
        return None


def _health_sort_key(
    completed: str | None, state: str | None, error_code: str | None
) -> tuple[bool, str, int, str]:
    """Latest completion time, then failing, stale, healthy, then error code."""

    return (
        completed is not None,
        completed or "",
        _HEALTH_STATE_RANK.get(state or "", 0),
        error_code or "",
    )


def select_latest_source_health(rows: Sequence[Sequence[Any]]) -> dict[str, SourceHealth]:
    """Keep one health record per source id.

    A later unusable state wins over an older usable row and is then omitted, so
    the catalog source stays unavailable instead of republishing stale health.
    """

    chosen: dict[str, tuple[tuple[bool, str, int, str], SourceHealth | None]] = {}
    for row in rows:
        if len(row) != len(_SOURCE_HEALTH_RESPONSE_FIELDS):
            continue
        source_id, completed_raw, error_raw, state_raw = row
        if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
            continue
        state = _operational_state(state_raw)
        completed = _render_utc(completed_raw)
        error_code = _allowlisted_error_code(error_raw)
        record = (
            None
            if state is None
            else SourceHealth(
                operational_state=state,
                last_completed_at=completed,
                last_error_code=error_code,
            )
        )
        key = _health_sort_key(completed, state, error_code)
        current = chosen.get(source_id)
        if current is None or key > current[0]:
            chosen[source_id] = (key, record)
    return {source_id: record for source_id, (_, record) in chosen.items() if record is not None}


def _with_source_health(source: FeedSource, record: SourceHealth | None) -> FeedSource:
    if record is None:
        return source
    try:
        return FeedSource.model_validate(
            {
                **source.model_dump(),
                "operational_state": record.operational_state,
                "diagnostic": None,
                "last_completed_at": record.last_completed_at,
                "last_error_code": record.last_error_code,
            }
        )
    except ValidationError:
        return source


class SnowflakeFeedRepository:
    def __init__(
        self, settings: ApiSettings, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._health_lock = threading.Lock()
        self._health_cache: _HealthCache | None = None
        self._suppressed_column_lock = threading.Lock()
        self._suppressed_column_cache: _ColumnPresenceCache | None = None

    def _feed_view(self) -> str:
        return (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}.INTELLIGENCE_FEED_V2"
        )

    def _feed_column_sql(self, suppressed_column: bool) -> str:
        columns = ", ".join(name.upper() for name in _FEED_RAW_PREFIX[:9])
        columns += (
            ", FIELD_STATES:published_at::VARCHAR AS PUBLICATION_DATE_STATE"
            ", IFF(IS_NULL_VALUE(PUBLISHER_METADATA:publisher), NULL,"
            " PUBLISHER_METADATA:publisher::VARCHAR) AS PUBLISHER"
            f", {_FEED_PUBLISHER_OBJECT}"
        )
        if suppressed_column:
            columns += f", {_ABSTRACT_SUPPRESSED_COLUMN}"
        columns += ", SOURCE_ID, REGISTRY_VERSION, ORGANIZATION, REVIEWED_TRUST_CLASSIFICATION"
        return columns

    def _abstract_suppressed_column_present(self, cursor: Any) -> bool:
        """Return whether ABSTRACT_SUPPRESSED exists, without a lookup on every read.

        A successful answer is cached for 300 seconds. A failed lookup is cached
        as absent for 60 seconds and does not raise.
        """

        with self._suppressed_column_lock:
            now = self._clock()
            cached = self._suppressed_column_cache
            if cached is not None and cached.expires_at > now:
                return cached.present
            try:
                present = self._query_abstract_suppressed_column(cursor)
            except Exception:
                self._suppressed_column_cache = _ColumnPresenceCache(
                    expires_at=now + ABSTRACT_SUPPRESSED_COLUMN_NEGATIVE_CACHE_TTL_SECONDS,
                    present=False,
                )
                return False
            self._suppressed_column_cache = _ColumnPresenceCache(
                expires_at=now + ABSTRACT_SUPPRESSED_COLUMN_CACHE_TTL_SECONDS,
                present=present,
            )
            return present

    def _query_abstract_suppressed_column(self, cursor: Any) -> bool:
        database = _sql_identifier(self.settings.presentation_database)
        cursor.execute(
            f"SELECT COLUMN_NAME FROM {database}.INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'INTELLIGENCE_FEED_V2' "
            "AND COLUMN_NAME = %s",
            (self.settings.snowflake_presentation_schema, _ABSTRACT_SUPPRESSED_COLUMN),
            timeout=self.settings.public_query_timeout_seconds,
        )
        return cursor.fetchone() is not None

    def _allowed_database(self) -> None:
        if self.settings.presentation_database not in {
            "ONE_HEALTH_LYME_GAP_ATLAS_DEV",
            "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        }:
            raise AtlasDataUnavailableError(UNAVAILABLE)

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
        } or kind not in {"items", "sources", "feed"}:
            raise AtlasDataUnavailableError(UNAVAILABLE)
        view_name = (
            "INTELLIGENCE_FEED_V2"
            if kind == "feed" or self.settings.intelligence_feed_projection_version == "v2"
            else "INTELLIGENCE_FEED_V"
        )
        view = (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}.{view_name}"
        )
        if kind == "sources":
            # /feed reads V2 independently of the legacy /items flag. Keep
            # identities from both published projections available to filters.
            namespace = (
                f"{_sql_identifier(self.settings.presentation_database)}."
                f"{_sql_identifier(self.settings.snowflake_presentation_schema)}"
            )
            source_columns = (
                "ITEM_ID, REVISION_ID, SOURCE_ID, REGISTRY_VERSION, FETCHED_AT, "
                "CONTENT_SHA256, ORGANIZATION, REVIEWED_TRUST_CLASSIFICATION, PUBLISHED_AT"
            )
            view = (
                f"(SELECT {source_columns} FROM {namespace}.INTELLIGENCE_FEED_V "
                f"UNION ALL SELECT {source_columns} FROM {namespace}.INTELLIGENCE_FEED_V2)"
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
        count = "COUNT(*)" if kind != "sources" else "COUNT(DISTINCT SOURCE_ID, REGISTRY_VERSION)"
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
        if kind == "feed":
            columns = self._feed_column_sql(False)
        if kind == "sources":
            order = "SOURCE_ID, REGISTRY_VERSION"
        elif kind == "feed":
            order = FEED_LIST_ORDER
        else:
            order = "ITEM_ID, REVISION_ID, SOURCE_ID, REGISTRY_VERSION"
        if kind == "sources":
            # One published row per source_id: highest registry version, then
            # organization and reviewed trust classification as a stable tie-break.
            identity = "SOURCE_ID, REGISTRY_VERSION, ORGANIZATION, REVIEWED_TRUST_CLASSIFICATION"
            statement = (
                f"SELECT {identity} FROM ("
                f"SELECT {identity}, ROW_NUMBER() OVER ("
                "PARTITION BY SOURCE_ID ORDER BY REGISTRY_VERSION DESC, "
                "ORGANIZATION, REVIEWED_TRUST_CLASSIFICATION"
                f") AS SOURCE_ROW FROM (SELECT DISTINCT {identity} FROM {view}{where}"
                ") DISTINCT_SOURCES) RANKED_SOURCES WHERE SOURCE_ROW = 1 "
                f"ORDER BY {order} LIMIT %s OFFSET %s"
            )
        else:
            statement = f"SELECT {columns} FROM {view}{where} ORDER BY {order} LIMIT %s OFFSET %s"
        suppressed_column = False
        try:
            with read_connection(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    state_sql, tuple(params), timeout=self.settings.public_query_timeout_seconds
                )
                before = cursor.fetchone()
                if before is None or before[0] > self.settings.public_query_result_ceiling:
                    raise PublicQueryError(
                        "QUERY_TOO_BROAD", "Narrow the source or publication dates."
                    )
                if kind == "feed":
                    suppressed_column = self._abstract_suppressed_column_present(cursor)
                    statement = (
                        f"SELECT {self._feed_column_sql(suppressed_column)} FROM {view}{where} "
                        f"ORDER BY {order} LIMIT %s OFFSET %s"
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
            if kind == "feed":
                rows = [_pack_feed_row(row, suppressed_column) for row in rows]
            marker = hashlib.sha256(json.dumps(before, default=str).encode()).hexdigest()
            return marker, rows
        except PublicQueryError:
            raise
        except Exception:
            raise AtlasDataUnavailableError(UNAVAILABLE) from None

    def read_item(self, item_id: str) -> list[tuple[Any, ...]]:
        self._allowed_database()
        view = self._feed_view()
        try:
            with read_connection(self.settings) as connection, connection.cursor() as cursor:
                suppressed_column = self._abstract_suppressed_column_present(cursor)
                statement = (
                    f"SELECT {self._feed_column_sql(suppressed_column)} FROM {view} "
                    "WHERE ITEM_ID = %s ORDER BY FETCHED_AT DESC, REVISION_ID DESC LIMIT 1"
                )
                cursor.execute(
                    statement,
                    (item_id,),
                    timeout=self.settings.public_query_timeout_seconds,
                )
                rows = cursor.fetchall()
            return [_pack_feed_row(row, suppressed_column) for row in rows]
        except PublicQueryError:
            raise
        except Exception:
            raise AtlasDataUnavailableError(UNAVAILABLE) from None

    def read_source_health(self) -> dict[str, SourceHealth] | None:
        """One bounded health read per cache miss. Failure is cached and returns None."""

        now = self._clock()
        with self._health_lock:
            cached = self._health_cache
            if cached is not None and cached.expires_at > now:
                return None if cached.value is None else dict(cached.value)
            try:
                value, row_count = self._query_source_health()
            except Exception as exc:
                logger.warning(
                    "intelligence_source_health_unavailable",
                    extra={"context": {"error_class": type(exc).__name__}},
                )
                self._health_cache = _HealthCache(
                    expires_at=now + SOURCE_HEALTH_NEGATIVE_CACHE_TTL_SECONDS,
                    value=None,
                )
                return None
            logger.info(
                "intelligence_source_health_read",
                extra={"context": {"row_count": row_count}},
            )
            self._health_cache = _HealthCache(
                expires_at=now + SOURCE_HEALTH_CACHE_TTL_SECONDS,
                value=value,
            )
            return dict(value)

    def _query_source_health(self) -> tuple[dict[str, SourceHealth], int]:
        if self.settings.presentation_database not in _PRESENTATION_DATABASES:
            raise AtlasDataUnavailableError(UNAVAILABLE)
        columns = ", ".join(
            _sql_identifier(INTELLIGENCE_SOURCE_HEALTH_COLUMNS[name])
            for name in _SOURCE_HEALTH_RESPONSE_FIELDS
        )
        view = (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}."
            f"{_sql_identifier(INTELLIGENCE_SOURCE_HEALTH_VIEW)}"
        )
        ceiling = self.settings.public_query_result_ceiling
        with read_connection(self.settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {columns} FROM {view} LIMIT %s",
                (ceiling + 1,),
                timeout=self.settings.public_query_timeout_seconds,
            )
            fetched = cursor.fetchall()
        if len(fetched) > ceiling:
            raise SourceHealthResultTruncated
        return select_latest_source_health(fetched), len(fetched)


class FeedService:
    def __init__(self, repository: FeedRepository) -> None:
        self.repository = repository

    def _feed_entries(self, rows: list[tuple[Any, ...]]) -> tuple[FeedEntry, ...]:
        entries: list[FeedEntry] = []
        withheld = 0
        for row in rows:
            prefix = dict(zip(_FEED_RAW_PREFIX, row[: len(_FEED_RAW_PREFIX)], strict=True))
            for field in ("provenance", "limitations"):
                if isinstance(prefix[field], str):
                    prefix[field] = json.loads(prefix[field])
            source = dict(zip(_SOURCE_FIELDS, row[-4:], strict=True))
            document, failures = project_feed_document(prefix, row[len(_FEED_RAW_PREFIX)], source)
            withheld += failures
            entries.append(FeedEntry.model_validate(document))
        if withheld:
            logger.info(
                "intelligence_feed_untrusted_text_withheld withheld_text_count=%s",
                withheld,
            )
        return tuple(entries)

    def entry(self, item_id: str) -> FeedEntry | None:
        rows = self.repository.read_item(item_id)
        if not rows:
            return None
        try:
            return self._feed_entries(rows[:1])[0]
        except AtlasDataUnavailableError:
            raise
        except Exception:
            raise AtlasDataUnavailableError(UNAVAILABLE) from None

    def search(
        self,
        kind: str,
        source_id: str | None,
        start: date | None,
        end: date | None,
        size: int,
        token: str | None,
    ) -> FeedItems | FeedSources | MetadataFeed:
        if start and end and start > end:
            raise PublicQueryError("INVALID_REQUEST", "Publication date range is reversed.")
        token_parts: list[Any] = [kind, source_id, str(start), str(end), size]
        if kind == "feed":
            token_parts.append(FEED_LIST_ORDER)
        fingerprint = hashlib.sha256(json.dumps(token_parts).encode()).hexdigest()
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
        health = self._load_source_health() if kind == "sources" else None
        meta = FeedMeta(next_page_token=next_token)
        try:
            if kind == "feed":
                return MetadataFeed(data=self._feed_entries(rows[:size]), meta=meta)
            if kind == "sources":
                catalog: list[FeedSource] = []
                for row in rows[:size]:
                    catalog_source = FeedSource.model_validate(
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
                    record = None if health is None else health.get(catalog_source.source_id)
                    catalog.append(_with_source_health(catalog_source, record))
                return FeedSources(
                    data=tuple(catalog),
                    meta=SourceCatalogMeta(
                        next_page_token=next_token,
                        source_health_available=health is not None,
                    ),
                )
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

    def _load_source_health(self) -> dict[str, SourceHealth] | None:
        reader = getattr(self.repository, "read_source_health", None)
        if not callable(reader):
            return None
        try:
            loaded = reader()
        except Exception as exc:
            logger.warning(
                "intelligence_source_health_unavailable",
                extra={"context": {"error_class": type(exc).__name__}},
            )
            return None
        if loaded is None:
            return None
        if isinstance(loaded, dict) and all(
            isinstance(key, str) and isinstance(value, SourceHealth)
            for key, value in loaded.items()
        ):
            return cast(dict[str, SourceHealth], loaded)
        logger.warning(
            "intelligence_source_health_unavailable",
            extra={"context": {"error_class": "SourceHealthMalformed"}},
        )
        return None


router = APIRouter(tags=["Intelligence feed"])


def _service(request: Request, response: Response, page_size: int | None) -> FeedService:
    settings = cast(ApiSettings, request.app.state.public_settings)
    response.headers["Cache-Control"] = "no-store"
    allowed: set[str] = set()
    if page_size is not None:
        allowed.update({"page_size", "page_token"})
        if request.url.path.endswith(("/items", "/feed")):
            allowed.update({"source_id", "published_from", "published_to"})
    if set(request.query_params) - allowed:
        raise PublicQueryError("INVALID_REQUEST", "Unknown query parameter.")
    if not settings.intelligence_feed_read_enabled:
        raise HTTPException(503, "Governed intelligence reading is not enabled.")
    if page_size is not None and page_size > settings.public_page_size_max:
        raise PublicQueryError("INVALID_REQUEST", "page_size exceeds the configured maximum.")
    return cast(FeedService, request.app.state.intelligence_feed_service)


def _missing_feed_entry(request: Request) -> JSONResponse:
    problem = ProblemDetails(
        type=problem_type_uri("intelligence-item-not-found"),
        title="Not found",
        status=404,
        detail="No intelligence feed entry exists for this id.",
        instance=request.url.path,
        request_id=getattr(request.state, "request_id", "unavailable"),
        code="INTELLIGENCE_ITEM_NOT_FOUND",
    )
    return JSONResponse(
        problem.model_dump(mode="json"),
        status_code=404,
        media_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/v1/intelligence/items",
    response_model=FeedItems,
    operation_id="listIntelligenceItems",
    summary="Read normalized publication intelligence",
    responses=FEED_PROBLEMS,
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
    responses=FEED_PROBLEMS,
    description="First-party read only. Each source_id appears once, with its highest "
    "registry_version. The catalog lists sources represented by published items, "
    "not the private registry or every registered source. operational_state is healthy, "
    "stale, or failing from PRESENTATION.INTELLIGENCE_SOURCE_HEALTH_V1, and unavailable "
    "with diagnostic SOURCE_HEALTH_NOT_PUBLISHED when that source has no health row or the "
    "health projection cannot be read. last_completed_at and last_error_code come from that "
    "projection. Run identifiers and free text are omitted. The health read is cached for "
    "5 minutes. page_size is 1–500 with state-bound tokens. The response must not be "
    "cached. No ingestion.",
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


@router.get(
    "/v1/intelligence/feed",
    response_model=MetadataFeed,
    operation_id="listIntelligenceFeed",
    summary="Read source-attributed publication metadata",
    responses=FEED_PROBLEMS,
    description="Read only from governed INTELLIGENCE_FEED_V2. "
    + _FEED_SUMMARY
    + " Publisher dates remain null when unknown; inclusive date filters exclude "
    "unknown dates. Entries with a publisher date are newest first. Entries with an "
    "unknown publisher date are last. Matching publisher dates use a stable tie-break "
    "of item id, revision id, source id, and registry version. Bounded "
    "state/filter-bound pagination, no-store. Disabled or unavailable returns 503; "
    "an available empty projection returns 200.",
)
def feed(
    request: Request,
    response: Response,
    source_id: str | None = Query(None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}$"),
    published_from: date | None = None,
    published_to: date | None = None,
    page_size: int = Query(100, ge=1, le=500),
    page_token: str | None = Query(None, max_length=2048),
) -> MetadataFeed:
    return cast(
        MetadataFeed,
        _service(request, response, page_size).search(
            "feed", source_id, published_from, published_to, page_size, page_token
        ),
    )


ENTRY_PROBLEMS = {
    **FEED_PROBLEMS,
    404: problem_response(
        404,
        "INTELLIGENCE_ITEM_NOT_FOUND",
        "No intelligence feed entry exists for this id.",
        description=(
            "The item id is not in the governed intelligence feed. "
            "The code is INTELLIGENCE_ITEM_NOT_FOUND."
        ),
    ),
}


@router.get(
    "/v1/intelligence/feed/{item_id}",
    response_model=FeedEntryRead,
    operation_id="getIntelligenceFeedEntry",
    summary="Read one source-attributed publication",
    responses=ENTRY_PROBLEMS,
    description=(
        "Read one current entry from governed INTELLIGENCE_FEED_V2 by its 64-character item id. "
        + _FEED_SUMMARY
        + " The entry uses the same fields as the feed list. Cache-Control is no-store. "
        "A malformed id returns 422. An id that is not in the feed returns 404. "
        "Disabled or unavailable returns 503."
    ),
)
def feed_entry(
    request: Request,
    response: Response,
    item_id: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")],
) -> FeedEntryRead | JSONResponse:
    found = _service(request, response, None).entry(item_id)
    if found is None:
        return _missing_feed_entry(request)
    return FeedEntryRead(data=found)
