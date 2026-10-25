"""Documentation text for schemas that no Atlas model class can describe directly.

Two kinds of schema need this. Generic response wrappers are generated under mechanical
names such as `CollectionEnvelope_Indicator_`. The score breakdown model belongs to the shared
scoring package, so this API describes its fields here instead of copying the model.

The text sets `title` on the generated wrappers and fills in missing `description` keys. It
never replaces a description that a model supplies and never changes types, required fields, or
any other part of a schema.
"""

from typing import Any

_COLLECTION_TEXT = {
    "CollectionEnvelope_Indicator_": (
        "Indicator collection",
        "A page of indicators, with paging details and links. "
        "Read `meta.next_page_token` to get the next page.",
    ),
    "CollectionEnvelope_Measure_": (
        "Measure collection",
        "A page of measures, with paging details and links. "
        "Read `meta.next_page_token` to get the next page.",
    ),
    "CollectionEnvelope_Source_": (
        "Source collection",
        "A page of sources, with paging details and links. "
        "Read `meta.next_page_token` to get the next page.",
    ),
    "CollectionEnvelope_Observation_": (
        "Standard Observation Collection",
        "A page of observations, with paging details and links. Read `meta.availability` and "
        "`meta.next_page_token` before you use `data`.",
    ),
    "CollectionEnvelope_CountyContextObservation_": (
        "County Context Observation Collection",
        "A page of county population or rurality observations, with paging details and links. "
        "Read `meta.availability` and `meta.next_page_token` before you use `data`.",
    ),
    "ResourceEnvelope_Indicator_": ("Indicator response", "One indicator, wrapped in `data`."),
    "ResourceEnvelope_Measure_": ("Measure response", "One measure, wrapped in `data`."),
    "ResourceEnvelope_Source_": ("Source response", "One source, wrapped in `data`."),
    "ResourceEnvelope_Methodology_": (
        "Methodology response",
        "One methodology, wrapped in `data`.",
    ),
    "ResourceEnvelope_Geography_": (
        "Geography response",
        "One county or state identity, wrapped in `data`. The identity carries no geometry.",
    ),
}

_SCORE_TEXT = (
    "The Surveillance Gap Score and the signals it combines. Every value runs from 0 to 100. "
    "Atlas results are not a diagnosis."
)
_SCORE_FIELDS = {
    "score": "Overall Surveillance Gap Score. A higher value means a higher review priority.",
    "human_weakness": (
        "How little human-case surveillance evidence the county has. A higher value means "
        "weaker human-case evidence. A county with no published count gets the configured "
        "default weakness."
    ),
    "ecological": (
        "Combined tick and pathogen signal: 60 percent `tick_signal` and 40 percent "
        "`pathogen_signal`."
    ),
    "community": (
        "Combined community signal: 50 percent `svi_signal`, 30 percent `access_signal`, and "
        "20 percent `rural_signal`."
    ),
    "tick_signal": (
        "Signal from the publisher's tick status: 100 for `Established`, 55 for `Reported`, "
        "and 0 for `No records` or `Unknown`. Neither 0 case establishes absence."
    ),
    "pathogen_signal": (
        "100 when the publisher reports the Lyme disease bacterium as `Present`, otherwise 0. "
        "A 0 does not establish absence."
    ),
    "svi_signal": "Social Vulnerability Index percentile of the county, scaled to 0 to 100.",
    "access_signal": (
        "Percentile of the county's uninsured share among counties, scaled to 0 to 100."
    ),
    "rural_signal": (
        "Rural-Urban Continuum Code of the county, scaled so the most urban code is 0 and the "
        "most rural code is 100."
    ),
}


def apply_schema_descriptions(schemas: dict[str, Any]) -> None:
    """Add missing titles and descriptions to generated and shared schemas in place."""
    for name, (title, description) in _COLLECTION_TEXT.items():
        schema = schemas.get(name)
        if schema is not None:
            schema["title"] = title
            schema.setdefault("description", description)
    score = schemas.get("Score")
    if score is not None:
        score.setdefault("description", _SCORE_TEXT)
        for field, description in _SCORE_FIELDS.items():
            if field in score.get("properties", {}):
                score["properties"][field].setdefault("description", description)
