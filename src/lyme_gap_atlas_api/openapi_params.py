"""Shared OpenAPI parameter declarations, defined once so the wording cannot drift.

Constraints (ranges, patterns, defaults) stay with the route or dependency that
enforces them. These annotations add only the public description and examples.
"""

from typing import Annotated

from fastapi import Path, Query

_SCORE_SETTING_NOTE = (
    "The published score always uses the default. "
    "Another value gives a what-if scenario, not the published score. "
    "A value outside the accepted range returns 422."
)

DatasetVersion = Annotated[
    str | None,
    Query(
        description=(
            "Data release to read, as a `release_id`. Omit it to use the current release. "
            "Copy a value from `release_id` in the metadata response. "
            "An unknown release returns 404."
        ),
        examples=["governed-2026-10-15-january-climate-prod"],
    ),
]

EcologicalShare = Annotated[
    int,
    Query(
        ge=40,
        le=85,
        multiple_of=5,
        description=(
            "Percent of the gap score weighted to ecological evidence (tick and pathogen "
            "signals). The rest goes to community vulnerability (social vulnerability, "
            "insurance access, and rurality). Use a multiple of 5 from 40 to 85. "
            "The default is 65. " + _SCORE_SETTING_NOTE
        ),
        examples=[65],
    ),
]

SmoothK = Annotated[
    int,
    Query(
        ge=5,
        le=200,
        description=(
            "Smoothing constant for the human-case signal. It sets the reported incidence at "
            "which the human weakness signal falls to 50: a larger value keeps the signal high "
            "for more counties. Use a whole number from 5 to 200. "
            "The default is 50. " + _SCORE_SETTING_NOTE
        ),
        examples=[50],
    ),
]

MissingHumanWeakness = Annotated[
    int,
    Query(
        ge=40,
        le=90,
        multiple_of=5,
        description=(
            "Human weakness signal (0-100) that Atlas assumes for a county with no published "
            "human case count or incidence. A missing count is not zero. "
            "Use a multiple of 5 from 40 to 90. "
            "The default is 75. " + _SCORE_SETTING_NOTE
        ),
        examples=[75],
    ),
]

LowIncidenceBreakpoint = Annotated[
    int | None,
    Query(
        deprecated=True,
        description=(
            "Deprecated. Do not send it. Any value returns 422. "
            "Use `smooth_k` to change how the human-case signal is smoothed."
        ),
    ),
]



_COUNTY_FIPS_DESCRIPTION = (
    "County FIPS code: exactly five digits, sent as a string with leading zeros kept, "
    "for example `01001`, not `1001`. Connecticut uses planning-region codes such as "
    "`09110`. A malformed value returns 422. A county not in the current release returns 404."
)

CountyFipsPath = Annotated[
    str,
    Path(pattern=r"^\d{5}$", description=_COUNTY_FIPS_DESCRIPTION, examples=["08001"]),
]
# Same text; this route validated with an ASCII-only pattern before, and keeps it.
CountyFipsAsciiPath = Annotated[
    str,
    Path(pattern=r"^[0-9]{5}$", description=_COUNTY_FIPS_DESCRIPTION, examples=["08001"]),
]
