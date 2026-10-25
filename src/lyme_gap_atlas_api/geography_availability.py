"""Canonical capability availability after current county identity validation."""

from typing import Literal

from pydantic import BaseModel, Field

from .public_contract import GeographyIdentity, GeographyType, PublicQueryError
from .repository import AtlasDataUnavailableError, Snapshot


class CanonicalGeographyAvailability(BaseModel):
    availability: Literal["UNAVAILABLE"]
    reason: Literal["CANONICAL_GEOGRAPHY_UNPUBLISHED"]
    data: None = Field(description="No canonical geography resource or geometry is fabricated.")
    release_id: str = Field(
        min_length=1, description="Governed county directory release used to confirm identity."
    )
    limitations: list[str] = Field(
        min_length=1,
        description="Publication and representation boundaries, not scientific absence.",
    )


class GeographyUnavailable(BaseModel):
    """Typed, successful answer that canonical geography identity is not published yet.

    Returned with HTTP 200 by the legacy identity route (ADR 0021). It carries no release
    because answering must stay instant and independent of the governed directory.
    """

    availability: Literal["UNAVAILABLE"]
    reason: Literal["CANONICAL_GEOGRAPHY_UNPUBLISHED"]
    data: None = Field(description="No canonical geography resource or geometry is fabricated.")
    geography: GeographyIdentity = Field(
        description="The validated identity that was requested, echoed back."
    )
    limitations: list[str] = Field(
        min_length=1,
        description="Publication and representation boundaries, not scientific absence.",
    )


def geography_unavailable(identity: GeographyIdentity) -> GeographyUnavailable:
    return GeographyUnavailable(
        availability="UNAVAILABLE",
        reason="CANONICAL_GEOGRAPHY_UNPUBLISHED",
        data=None,
        geography=identity,
        limitations=[
            "Canonical geography identity delivery is not published by this API capability.",
            "The requested identity is validated for shape only; membership in the current "
            "release is not asserted (use the county availability route for that). "
            "No canonical, display, or analysis geometry is returned or inferred.",
        ],
    )


def county_availability(snapshot: Snapshot, county_fips: str) -> CanonicalGeographyAvailability:
    release = snapshot.metadata.release_id
    if not release.strip() or not snapshot.counties:
        raise AtlasDataUnavailableError("Current geography authority is unavailable")
    identities = set()
    for county in snapshot.counties:
        try:
            GeographyIdentity(geography_type=GeographyType.county, geography_id=county.fips)
        except ValueError:
            raise AtlasDataUnavailableError("Current geography authority is unavailable") from None
        if county.release_id != release or county.fips in identities:
            raise AtlasDataUnavailableError("Current geography authority is unavailable")
        identities.add(county.fips)
    if county_fips not in identities:
        raise PublicQueryError(
            "RESOURCE_NOT_FOUND", "County is not in the current geography release."
        )
    return CanonicalGeographyAvailability(
        availability="UNAVAILABLE",
        reason="CANONICAL_GEOGRAPHY_UNPUBLISHED",
        data=None,
        release_id=release,
        limitations=[
            "Canonical geography identity delivery is not published by this API capability.",
            "County membership is confirmed from the governed county directory release. "
            "No canonical, display, or analysis geometry is returned or inferred.",
        ],
    )
