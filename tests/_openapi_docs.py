"""Helpers that compare OpenAPI schema shape without documentation wording."""

from typing import Any

DOCUMENTATION_KEYS = frozenset({"description", "title", "examples", "example"})


def without_documentation(value: Any, *, in_property_map: bool = False) -> Any:
    """Drop wording-only keys so a test pins shape, not documentation text.

    Keys inside a `properties` map are property names, so they are kept.
    """
    if isinstance(value, dict):
        return {
            key: without_documentation(
                item, in_property_map=key == "properties" and not in_property_map
            )
            for key, item in value.items()
            if in_property_map or key not in DOCUMENTATION_KEYS
        }
    if isinstance(value, list):
        return [without_documentation(item) for item in value]
    return value
