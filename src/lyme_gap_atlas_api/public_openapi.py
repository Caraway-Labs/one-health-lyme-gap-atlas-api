"""Fail-closed external projection of the complete first-party HTTP schema."""

from copy import deepcopy
from typing import Any

from .public_docs import API_DESCRIPTION, API_SUMMARY, TAGS

PUBLIC_PATHS = frozenset(
    {
        "/v1/indicators",
        "/v1/indicators/{indicator_id}",
        "/v1/measures",
        "/v1/measures/{measure_id}",
        "/v1/observations",
        "/v1/sources",
        "/v1/sources/{source_id}",
        "/v1/methodologies/{methodology_id}",
        "/v1/geographies/{geography_type}/{geography_id}",
        "/v1/atlas/metadata",
        "/v1/atlas/geometry",
        "/v1/atlas/scores",
        "/v1/atlas/ranking.csv",
        "/v1/counties/{fips}",
        "/v1/counties/{fips}/report.pdf",
        "/v1/states/{state}/report.pdf",
    }
)


def public_projection(complete: dict[str, Any]) -> dict[str, Any]:
    """Retain approved GETs and only their reachable components, without mutation."""
    schema = deepcopy(complete)
    schema["info"]["summary"] = API_SUMMARY
    schema["info"]["description"] = API_DESCRIPTION
    schema["tags"] = deepcopy(TAGS)
    schema["paths"] = {path: {"get": schema["paths"][path]["get"]} for path in sorted(PUBLIC_PATHS)}
    components = schema.pop("components", {})
    retained: dict[str, dict[str, Any]] = {}
    visited: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/") and ref not in visited:
                visited.add(ref)
                _, _, group, name = ref.split("/", 3)
                component = components[group][name]
                retained.setdefault(group, {})[name] = component
                visit(component)
            for item in value.values():
                visit(item)

    visit(schema)
    schema["components"] = retained
    return schema
