"""Strict public v2 projection; private source-native metadata is never accepted."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .briefings import (
    BriefingModel,
    Digest,
    IntelligenceFieldStates,
    IntelligenceItem,
    IntelligenceTag,
    State,
    Text,
    Token,
    Utc,
)

MetadataText = Annotated[str, Field(max_length=4096, pattern=r"^[^<>\x00-\x1f]*$")]


class PublisherDateStates(BriefingModel):
    published_at: State
    updated_at: State


class PublisherMedia(BriefingModel):
    url: Annotated[str, Field(min_length=1, max_length=4096)]
    origin: Literal["publisher"]

    @model_validator(mode="after")
    def public_reference(self) -> Self:
        from urllib.parse import parse_qsl, urlsplit

        value = urlsplit(self.url)
        host = value.hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if (
            value.scheme != "https"
            or not value.hostname
            or value.username
            or value.password
            or value.fragment
            or value.port not in {None, 443}
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", host)
            or host.lower() == "localhost"
            or host.lower().endswith((".localhost", ".local", ".internal"))
            or host.endswith(".")
            or ".." in host
            or (
                address is None
                and (
                    "." not in host
                    or re.fullmatch(
                        r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*", host.lower()
                    )
                )
            )
            or (address is not None and not address.is_global)
            or any(char.isspace() or ord(char) < 32 for char in self.url)
            or any(
                key.lower()
                in {
                    "token",
                    "access_token",
                    "api_key",
                    "apikey",
                    "password",
                    "secret",
                    "email",
                    "recipient",
                    "authorization",
                    "signature",
                    "sig",
                    "googleaccessid",
                    "key-pair-id",
                }
                or key.lower().startswith(("x-amz-", "x-goog-"))
                for key, _ in parse_qsl(value.query)
            )
        ):
            raise ValueError("INTELLIGENCE_MEDIA_PUBLIC_REFERENCE_REQUIRED")
        return self


class PublisherMetadata(BriefingModel):
    publisher: Text | None
    source_family: Literal[
        "cdc_mmwr", "cdc_eid", "cdc_other", "nih_niaid", "pubmed", "state_local", "other"
    ]
    source_item_id: MetadataText | None
    authors: Annotated[tuple[MetadataText, ...], Field(max_length=100)]
    categories: Annotated[tuple[MetadataText, ...], Field(max_length=100)]
    language: Annotated[str, Field(max_length=64, pattern=r"^[^<>\x00-\x1f]*$")] | None
    media: Annotated[tuple[PublisherMedia, ...], Field(max_length=100)]
    date_states: PublisherDateStates


class DerivedMetadata(BriefingModel):
    """Empty until a separately authorized versioned enrichment contract exists."""


class ProjectionProvenance(BriefingModel):
    run_id: Token
    artifact_id: Token
    artifact_sha256: Digest
    parser_version: Literal["rss-atom-native-v2"]
    fetch_version: Token
    normalization_version: Literal["intelligence-identity-v2"]


class IntelligenceItemProjectionV2(BriefingModel):
    """Canonical public fields; no native tree/raw dates/rights receipts or XML."""

    contract_version: Literal["2.0.0"]
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
    transport: Literal["rss", "atom"]
    transport_identity_sha256: Digest
    deduplication_key: Digest
    content_sha256: Digest
    provenance: ProjectionProvenance
    limitations: Annotated[tuple[Text, ...], Field(max_length=100)]
    content_is_untrusted: Literal[True]
    publisher_metadata: PublisherMetadata
    derived_metadata: DerivedMetadata

    @model_validator(mode="after")
    def normalized_semantics(self) -> Self:
        # Reuse v1 chronology/missingness/untrusted-text semantics without changing
        # the API90 briefing/digest contract or relabelling the returned item.
        document = self.model_dump(exclude={"publisher_metadata", "derived_metadata"})
        document["contract_version"] = "1.0.0"
        document["provenance"]["normalization_version"] = "intelligence-identity-v1"
        IntelligenceItem.model_validate(document)
        if (
            self.publisher_metadata.date_states.published_at != self.field_states.published_at
            or self.publisher_metadata.date_states.updated_at != self.field_states.updated_at
            or any(tag.origin != "publisher" for tag in (*self.topics, *self.geographies))
        ):
            raise ValueError("INTELLIGENCE_PUBLISHER_PROJECTION_INVALID")
        return self
