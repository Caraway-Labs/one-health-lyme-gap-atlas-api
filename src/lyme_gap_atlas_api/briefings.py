"""Versioned, evidence-only digest artifacts; no delivery or live source wiring.

The API validates and preserves DATA #131 normalized records. It never fetches,
renormalizes, infers geography, approves sources or calls a language model.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from threading import Lock
from typing import Annotated, Any, Final, Literal, Protocol, Self
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import models_json_schema

Token = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=1000, pattern=r"^[^<>\x00-\x1f]*$")]


def _utc(value: str) -> str:
    # Strict UTC strings preserve DATA's arbitrary fractional precision.
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z", value
    ):
        raise ValueError("BRIEFING_UTC_REQUIRED")
    datetime.fromisoformat(value[:19])  # Calendar/seconds validation, no fractional truncation.
    fraction = value[20:-1] if len(value) > 20 else ""
    if fraction.endswith("0"):
        raise ValueError("BRIEFING_CANONICAL_UTC_REQUIRED")
    return value


Utc = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"),
    AfterValidator(_utc),
]
State = Literal["present", "not_provided", "withheld", "invalid"]


class BriefingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IntelligenceFieldStates(BriefingModel):
    canonical_url: State
    title: State
    published_at: State
    updated_at: State
    event_at: State
    excerpt: State


class IntelligenceTag(BriefingModel):
    value: Token
    origin: Literal["publisher", "inferred"]
    method: Token | None
    method_version: Token | None
    confidence: Annotated[float, Field(ge=0, le=1, strict=True)] | None

    @model_validator(mode="after")
    def origin_evidence(self) -> Self:
        if self.origin == "publisher" and any(
            value is not None for value in (self.method, self.method_version, self.confidence)
        ):
            raise ValueError("BRIEFING_PUBLISHER_TAG_EVIDENCE_INVALID")
        if self.origin == "inferred" and (self.method is None or self.method_version is None):
            raise ValueError("BRIEFING_INFERENCE_EVIDENCE_REQUIRED")
        return self


class IntelligenceProvenance(BriefingModel):
    run_id: Token
    artifact_id: Token
    artifact_sha256: Digest
    parser_version: Token
    fetch_version: Token
    normalization_version: Literal["intelligence-identity-v1"]


def public_canonical_url(value: str) -> str:
    """Validate the existing canonical HTTPS link boundary for feed consumers."""
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.fragment
        or url.port not in {None, 443}
        or re.search(r"[\x00-\x20\x7f]", value)
    ):
        raise ValueError("BRIEFING_PUBLIC_URL_REQUIRED")
    return value


class IntelligenceItem(BriefingModel):
    """Typed consumer of normalized DATA #131 v1 items, without renormalization."""

    contract_version: Literal["1.0.0"]
    item_id: Digest
    revision_id: Digest
    source_id: Token
    registry_version: Annotated[int, Field(ge=1, strict=True)]
    canonical_url: Annotated[str, Field(max_length=4096)] | None
    title: Text | None
    published_at: Utc | None
    updated_at: Utc | None
    event_at: Utc | None
    excerpt: (
        Annotated[
            str, Field(min_length=1, max_length=5000, pattern=r"^[^<>\x00-\x08\x0b\x0c\x0e-\x1f]*$")
        ]
        | None
    )
    fetched_at: Utc
    field_states: IntelligenceFieldStates
    geographies: Annotated[tuple[IntelligenceTag, ...], Field(max_length=100)]
    topics: Annotated[tuple[IntelligenceTag, ...], Field(max_length=100)]
    transport: Literal["rss", "atom", "email", "web"]
    transport_identity_sha256: Digest
    deduplication_key: Digest
    content_sha256: Digest
    provenance: IntelligenceProvenance
    limitations: Annotated[tuple[Text, ...], Field(max_length=100)]
    content_is_untrusted: Literal[True]

    @model_validator(mode="after")
    def normalized_semantics(self) -> Self:
        for name, state in self.field_states.model_dump().items():
            if (getattr(self, name) is not None) != (state == "present"):
                raise ValueError("BRIEFING_FIELD_STATE_MISMATCH")
        if self.item_id != self.deduplication_key:
            raise ValueError("BRIEFING_ITEM_IDENTITY_MISMATCH")
        if self.canonical_url is not None:
            public_canonical_url(self.canonical_url)
        if any(
            len(values) != len(set(values))
            for values in (self.topics, self.geographies, self.limitations)
        ):
            raise ValueError("BRIEFING_DUPLICATE_METADATA")
        return self


class BriefingSource(BriefingModel):
    source_id: Token
    registry_version: Annotated[int, Field(ge=1, strict=True)]
    organization: Text
    reviewed_trust_classification: Literal[
        "official_public_health", "research_index", "reviewed_other"
    ]


class BriefingWindow(BriefingModel):
    start: Utc = Field(description="Inclusive UTC capture retrieval cutoff.")
    end: Utc = Field(description="Exclusive UTC capture retrieval cutoff.")
    basis: Literal["fetched_at"] = "fetched_at"

    @model_validator(mode="after")
    def bounded_window(self) -> Self:
        if _timestamp(self.start) >= _timestamp(self.end):
            raise ValueError("BRIEFING_WINDOW_INVALID")
        delta = datetime.fromisoformat(self.end[:19]) - datetime.fromisoformat(self.start[:19])
        if delta > timedelta(days=31) or (
            delta == timedelta(days=31) and _timestamp(self.end)[1] > _timestamp(self.start)[1]
        ):
            raise ValueError("BRIEFING_WINDOW_LIMIT")
        return self


class BriefingScope(BriefingModel):
    geographies: Annotated[tuple[Token, ...], Field(max_length=100)] = ()
    topics: Annotated[tuple[Token, ...], Field(max_length=100)] = ()
    sources: Annotated[tuple[Token, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def unique_preferences(self) -> Self:
        if any(
            len(values) != len(set(values))
            for values in (self.geographies, self.topics, self.sources)
        ):
            raise ValueError("BRIEFING_DUPLICATE_PREFERENCE")
        return self


class BriefingGenerationRequest(BriefingModel):
    contract_version: Literal["1.0.0"] = "1.0.0"
    mode: Literal["digest"] = "digest"
    subscriber_key: Digest = Field(
        description="Private opaque subscriber key; never an email address."
    )
    preference_revision: Token
    digest_version: Token
    window: BriefingWindow
    scope: BriefingScope
    max_items: Annotated[int, Field(ge=1, le=100, strict=True)] = 20


class IntelligenceSnapshot(BriefingModel):
    snapshot_id: Digest
    as_of: Utc
    items: Annotated[tuple[IntelligenceItem, ...], Field(max_length=1000)]
    sources: Annotated[tuple[BriefingSource, ...], Field(max_length=1000)]
    limitations: Annotated[tuple[Text, ...], Field(max_length=100)] = ()


class BriefingMatch(BriefingModel):
    dimension: Literal["geography", "topic", "source", "unfiltered"]
    value: Token
    origin: Literal["publisher", "inferred", "source_identity", "unfiltered"]


class BriefingEvidence(BriefingModel):
    item: IntelligenceItem
    source: BriefingSource
    why_matched: tuple[BriefingMatch, ...]


class RankedBriefingItem(BriefingModel):
    rank: Annotated[int, Field(ge=1)]
    item_id: Digest
    matching_values: Annotated[int, Field(ge=0)]
    publisher_recency: Utc | None
    evidence: tuple[BriefingEvidence, ...]


DIGEST_NOTICE: Final = (
    "Periodic intelligence digest; not an official public-health alert or a disease-risk estimate."
)


class BriefingArtifact(BriefingModel):
    contract_version: Literal["1.0.0"] = "1.0.0"
    generation_version: Literal["deterministic-digest-v1"] = "deterministic-digest-v1"
    mode: Literal["digest"] = "digest"
    notice: Literal[
        "Periodic intelligence digest; not an official public-health alert "
        "or a disease-risk estimate."
    ] = DIGEST_NOTICE
    artifact_id: Digest
    input_sha256: Digest
    snapshot_id: Digest
    generated_at: Utc
    preference_revision: Token
    digest_version: Token
    window: BriefingWindow
    scope: BriefingScope
    ranked_items: tuple[RankedBriefingItem, ...]
    matching_publications: Annotated[int, Field(ge=0)]
    omitted_publications: Annotated[int, Field(ge=0)]
    limitations: tuple[Text, ...]


class BriefingError(RuntimeError):
    """Fixed diagnostic codes; never provider messages or source content."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _request_document(request: BriefingGenerationRequest) -> dict[str, Any]:
    document = request.model_dump(mode="json")
    document["scope"] = {name: sorted(values) for name, values in document["scope"].items()}
    return document


def _timestamp(value: str) -> tuple[str, str]:
    return value[:19], value[20:-1] if len(value) > 20 else ""


def _matches(item: IntelligenceItem, scope: BriefingScope) -> tuple[BriefingMatch, ...] | None:
    matches: list[BriefingMatch] = []
    for dimension, selected, tags in (
        ("geography", scope.geographies, item.geographies),
        ("topic", scope.topics, item.topics),
    ):
        found = [tag for tag in tags if tag.value in selected]
        if selected and not found:
            return None
        matches.extend(
            BriefingMatch.model_validate(
                {"dimension": dimension, "value": tag.value, "origin": tag.origin}
            )
            for tag in found
        )
    if scope.sources:
        if item.source_id not in scope.sources:
            return None
        matches.append(
            BriefingMatch(dimension="source", value=item.source_id, origin="source_identity")
        )
    if not any((scope.geographies, scope.topics, scope.sources)):
        matches.append(
            BriefingMatch(dimension="unfiltered", value="all_available", origin="unfiltered")
        )
    return tuple(
        sorted(set(matches), key=lambda value: (value.dimension, value.value, value.origin))
    )


class BriefingArtifactStore(Protocol):
    """Implementations must atomically insert-if-absent and return the stored winner."""

    def insert_if_absent(self, artifact: BriefingArtifact) -> BriefingArtifact: ...


class InMemoryBriefingStore:
    """Explicit offline/test store; never default production persistence."""

    def __init__(self) -> None:
        self._artifacts: dict[str, BriefingArtifact] = {}
        self._lock = Lock()

    def insert_if_absent(self, artifact: BriefingArtifact) -> BriefingArtifact:
        with self._lock:
            stored = self._artifacts.setdefault(artifact.artifact_id, artifact)
            if stored.input_sha256 != artifact.input_sha256:
                raise BriefingError("BRIEFING_IDEMPOTENCY_CONFLICT")
            return stored


class BriefingService:
    def __init__(self, store: BriefingArtifactStore) -> None:
        self.store = store

    def generate(
        self,
        request: BriefingGenerationRequest,
        snapshot: IntelligenceSnapshot,
        *,
        generated_at: str,
    ) -> BriefingArtifact:
        if len(_canonical(snapshot.model_dump(mode="json")).encode("utf-8")) > 10_000_000:
            raise BriefingError("BRIEFING_SNAPSHOT_LIMIT")
        if _timestamp(_utc(generated_at)) < _timestamp(snapshot.as_of):
            raise BriefingError("BRIEFING_GENERATION_BEFORE_SNAPSHOT")
        sources = {
            (source.source_id, source.registry_version): source for source in snapshot.sources
        }
        if len(sources) != len(snapshot.sources):
            raise BriefingError("BRIEFING_SOURCE_VERSION_AMBIGUOUS")
        if _timestamp(snapshot.as_of) < _timestamp(request.window.end):
            raise BriefingError("BRIEFING_SNAPSHOT_BEFORE_CUTOFF")
        documents = sorted({_canonical(item.model_dump(mode="json")) for item in snapshot.items})
        source_documents = sorted(
            _canonical(source.model_dump(mode="json")) for source in snapshot.sources
        )
        request_document = _request_document(request)
        artifact_id = _hash(
            {
                "subscriber_key": request.subscriber_key,
                "window": request_document["window"],
                "digest_version": request.digest_version,
                "preference_revision": request.preference_revision,
                "contract_version": request.contract_version,
                "generation_version": "deterministic-digest-v1",
                "mode": "digest",
            }
        )
        input_hash = _hash(
            {
                "request": request_document,
                "snapshot_id": snapshot.snapshot_id,
                "as_of": snapshot.as_of,
                "items": documents,
                "sources": source_documents,
                "limitations": sorted(set(snapshot.limitations)),
            }
        )
        grouped: dict[str, list[BriefingEvidence]] = {}
        capture_documents: dict[tuple[str, ...], str] = {}
        for document in documents:
            item = IntelligenceItem.model_validate_json(document)
            source = sources.get((item.source_id, item.registry_version))
            if source is None:
                raise BriefingError("BRIEFING_SOURCE_VERSION_REQUIRED")
            if _timestamp(item.fetched_at) > _timestamp(snapshot.as_of):
                raise BriefingError("BRIEFING_CAPTURE_AFTER_SNAPSHOT")
            capture = (
                item.provenance.run_id,
                item.provenance.artifact_id,
                item.source_id,
                str(item.registry_version),
                item.item_id,
                item.revision_id,
            )
            if capture in capture_documents and capture_documents[capture] != document:
                raise BriefingError("BRIEFING_CAPTURE_CONFLICT")
            capture_documents[capture] = document
            if not (
                _timestamp(request.window.start)
                <= _timestamp(item.fetched_at)
                < _timestamp(request.window.end)
            ):
                continue
            why = _matches(item, request.scope)
            if why is not None:
                grouped.setdefault(item.item_id, []).append(
                    BriefingEvidence(item=item, source=source, why_matched=why)
                )
        publications: list[RankedBriefingItem] = []
        for item_id, evidence in grouped.items():
            match_values = {
                (match.dimension, match.value)
                for capture in evidence
                for match in capture.why_matched
                if match.dimension != "unfiltered"
            }
            dates = [
                date
                for capture in evidence
                for date in (capture.item.published_at, capture.item.updated_at)
                if date is not None
            ]
            publications.append(
                RankedBriefingItem(
                    rank=1,
                    item_id=item_id,
                    matching_values=len(match_values),
                    publisher_recency=max(dates, key=_timestamp) if dates else None,
                    evidence=tuple(evidence),
                )
            )
        publications.sort(key=lambda publication: publication.item_id)
        publications.sort(
            key=lambda publication: (
                _timestamp(publication.publisher_recency)
                if publication.publisher_recency
                else ("", "")
            ),
            reverse=True,
        )
        publications.sort(key=lambda publication: publication.matching_values, reverse=True)
        ranked = tuple(
            publication.model_copy(update={"rank": rank})
            for rank, publication in enumerate(publications[: request.max_items], 1)
        )
        limitations = (
            "Bounded capture window uses retrieval time; publisher chronology remains separate.",
            "No matches do not establish absence of disease or complete publisher coverage.",
            "Ranking uses exact preference matches, publisher recency and stable item ID; "
            "trust categories are unweighted.",
            "Publisher text is untrusted; no generated clinical claims or inferred geography.",
            "Cross-window delivery suppression requires the separate subscriber delivery ledger.",
            "Source-health and actual source cadence evidence require DATA 136.",
            *sorted(set(snapshot.limitations)),
        )
        artifact = BriefingArtifact(
            artifact_id=artifact_id,
            input_sha256=input_hash,
            snapshot_id=snapshot.snapshot_id,
            generated_at=generated_at,
            preference_revision=request.preference_revision,
            digest_version=request.digest_version,
            window=request.window,
            scope=BriefingScope.model_validate(request_document["scope"]),
            ranked_items=ranked,
            matching_publications=len(publications),
            omitted_publications=max(0, len(publications) - request.max_items),
            limitations=tuple(dict.fromkeys(limitations)),
        )
        try:
            stored = self.store.insert_if_absent(artifact)
            stored = BriefingArtifact.model_validate(stored.model_dump(mode="json"))
            if _timestamp(stored.generated_at) < _timestamp(snapshot.as_of):
                raise BriefingError("BRIEFING_STORE_RECEIPT_INVALID")
            if stored.model_dump(exclude={"generated_at"}) != artifact.model_dump(
                exclude={"generated_at"}
            ):
                raise BriefingError("BRIEFING_STORE_RECEIPT_INVALID")
        except BriefingError as error:
            code = (
                str(error)
                if str(error) in {"BRIEFING_IDEMPOTENCY_CONFLICT", "BRIEFING_STORE_RECEIPT_INVALID"}
                else "BRIEFING_STORE_UNAVAILABLE"
            )
        except Exception:
            code = "BRIEFING_STORE_UNAVAILABLE"
        else:
            return stored
        raise BriefingError(code) from None


def add_briefing_openapi(schema: dict[str, Any]) -> None:
    """Document internal artifact contracts without introducing subscriber routes."""
    _, document = models_json_schema(
        [
            (BriefingGenerationRequest, "validation"),
            (IntelligenceSnapshot, "validation"),
            (BriefingArtifact, "serialization"),
        ],
        ref_template="#/components/schemas/{model}",
    )
    schema.setdefault("components", {}).setdefault("schemas", {}).update(document["$defs"])
