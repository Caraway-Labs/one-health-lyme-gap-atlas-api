"""Strict public v2 projection; private source-native metadata is never accepted."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Final, Literal, Self

from pydantic import Field, StrictBool, ValidationError, model_validator

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


_PlainLabel = r"^[^<>\x00-\x1f\x7f]*$"


class StoredPublisherDescription(BriefingModel):
    """FEED_V2 description object. Text and state rules are the /feed ones."""

    text: str | None
    state: Literal["present", "empty", "not_provided"]
    format: Literal["plain_text"]
    origin: Literal["publisher"]

    @model_validator(mode="after")
    def feed_text_rules(self) -> Self:
        from .intelligence_feed import FeedDescription

        try:
            FeedDescription.model_validate({"text": self.text, "state": self.state})
        except ValidationError:
            # The row text is not repeated in the error.
            raise ValueError("INTELLIGENCE_PUBLISHER_DESCRIPTION_INVALID") from None
        return self


class StoredPubMedAbstract(BriefingModel):
    """FEED_V2 abstract object. Text, state and copyright rules are the /feed ones.

    A V175 suppression replaces the object with text, state and copyright_line
    only, so attribution and policy_url may be absent, but never different.
    """

    text: str | None
    state: Literal["present", "not_provided", "suppressed", "withheld"]
    copyright_line: str | None
    attribution: Literal["Courtesy of the U.S. National Library of Medicine"] = (
        "Courtesy of the U.S. National Library of Medicine"
    )
    policy_url: Literal["https://www.ncbi.nlm.nih.gov/home/about/policies/"] = (
        "https://www.ncbi.nlm.nih.gov/home/about/policies/"
    )

    @model_validator(mode="after")
    def feed_text_rules(self) -> Self:
        from .intelligence_feed import FeedAbstract

        try:
            FeedAbstract.model_validate(
                {"text": self.text, "state": self.state, "copyright_line": self.copyright_line}
            )
        except ValidationError:
            raise ValueError("INTELLIGENCE_PUBLISHER_ABSTRACT_INVALID") from None
        return self


class PublisherMeshHeading(BriefingModel):
    name: Annotated[str, Field(min_length=1, max_length=1000, pattern=_PlainLabel)]
    ui: Annotated[str, Field(pattern=r"^[A-Z][0-9]{6}$")]


MatchedQuery = Literal["pubmed-tick-borne", "pubmed-lyme-borrelia"]


class FeedV2PublisherViewOnly(BriefingModel):
    """publisher_metadata keys added by the FEED_V2 view (data V173 and V175).

    Each may be missing or null. They are checked strictly and never returned.
    """

    description: StoredPublisherDescription | None = None
    abstract: StoredPubMedAbstract | None = None
    abstract_suppressed: StrictBool | None = None
    mesh: Annotated[tuple[PublisherMeshHeading, ...], Field(max_length=50)] | None = None
    publication_types: (
        Annotated[
            tuple[Annotated[str, Field(min_length=1, max_length=256, pattern=_PlainLabel)], ...],
            Field(max_length=50),
        ]
        | None
    ) = None
    matched_queries: Annotated[tuple[MatchedQuery, ...], Field(max_length=2)] | None = None

    @model_validator(mode="after")
    def distinct_matched_queries(self) -> Self:
        if self.matched_queries is not None and len(set(self.matched_queries)) != len(
            self.matched_queries
        ):
            raise ValueError("INTELLIGENCE_PUBLISHER_MATCHED_QUERIES_INVALID")
        return self


FEED_V2_VIEW_ONLY_KEYS: Final[frozenset[str]] = frozenset(FeedV2PublisherViewOnly.model_fields)


# The FEED_V2 view also carries description, abstract, abstract_suppressed, mesh,
# publication_types and matched_queries. PublisherMetadata validates them strictly
# and then drops them, so they are not returned and not in the schema. Any other
# key is still rejected. (A class docstring would become a schema description.)
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

    @model_validator(mode="before")
    @classmethod
    def view_only_keys(cls, data: object) -> object:
        """Validate the FEED_V2 keys this contract does not return, then drop them."""
        if not isinstance(data, dict):
            return data
        present = {key: data[key] for key in FEED_V2_VIEW_ONLY_KEYS if key in data}
        if not present:
            return data
        try:
            FeedV2PublisherViewOnly.model_validate(present)
        except ValidationError:
            # Field values come from an untrusted row and are not repeated.
            raise ValueError("INTELLIGENCE_PUBLISHER_METADATA_INVALID") from None
        return {key: value for key, value in data.items() if key not in FEED_V2_VIEW_ONLY_KEYS}


class DerivedMetadata(BriefingModel):
    """Empty until a separately authorized versioned enrichment contract exists."""


class ProjectionProvenance(BriefingModel):
    run_id: Token
    artifact_id: Token
    artifact_sha256: Digest
    parser_version: Literal["rss-atom-native-v2", "entrez-efetch-v1"]
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
