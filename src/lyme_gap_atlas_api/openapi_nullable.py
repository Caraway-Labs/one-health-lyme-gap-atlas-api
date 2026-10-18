"""Safe normalization for Pydantic's nullable OpenAPI 3.1 unions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_ANNOTATIONS = {
    "$comment",
    "default",
    "deprecated",
    "description",
    "example",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
}
_TYPE_KEYWORDS = {
    "array": {
        "contains",
        "items",
        "maxContains",
        "maxItems",
        "minContains",
        "minItems",
        "prefixItems",
        "uniqueItems",
    },
    "boolean": set(),
    "integer": {"maximum", "minimum", "multipleOf", "exclusiveMaximum", "exclusiveMinimum"},
    "number": {"maximum", "minimum", "multipleOf", "exclusiveMaximum", "exclusiveMinimum"},
    "object": {
        "additionalProperties",
        "dependentRequired",
        "dependentSchemas",
        "maxProperties",
        "minProperties",
        "patternProperties",
        "properties",
        "propertyNames",
        "required",
        "unevaluatedProperties",
    },
    "string": {
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "format",
        "maxLength",
        "minLength",
        "pattern",
    },
}


def _merge_annotations(target: dict[str, Any], source: Mapping[str, Any]) -> bool:
    """Merge annotation keywords without silently choosing conflicting docs."""

    for key, value in source.items():
        if key not in _ANNOTATIONS:
            return False
        if key in target and target[key] != value:
            return False
        target[key] = value
    return True


def _nullable_type_array(schema: Mapping[str, Any]) -> dict[str, Any] | None:
    branches = schema.get("anyOf")
    if not isinstance(branches, list) or len(branches) < 2:
        return None
    null_branches = [branch for branch in branches if branch == {"type": "null"}]
    typed_branches = [
        branch
        for branch in branches
        if isinstance(branch, dict) and branch.get("type") != "null"
    ]
    if len(null_branches) != 1 or len(typed_branches) != len(branches) - 1:
        return None

    result = {key: value for key, value in schema.items() if key != "anyOf"}
    if any(key not in _ANNOTATIONS for key in result):
        return None

    types: list[str] = []
    normalized_branches: list[dict[str, Any]] = []
    for branch in typed_branches:
        kind = branch.get("type")
        if not isinstance(kind, str) or kind not in _TYPE_KEYWORDS:
            return None
        if kind in types:
            return None
        types.append(kind)
        normalized = dict(branch)
        normalized.pop("type")
        keys = set(normalized)
        allowed = _ANNOTATIONS | _TYPE_KEYWORDS[kind]
        if len(typed_branches) == 1:
            allowed |= {"enum", "const"}
        if not keys <= allowed or ({"enum", "const"} <= keys):
            return None
        normalized_branches.append(normalized)

    if len(typed_branches) > 1 and any(
        branch != normalized_branches[0] for branch in normalized_branches[1:]
    ):
        # Different per-type constraints cannot be flattened without changing
        # which branch each constraint governs.
        return None

    for branch in normalized_branches:
        for key, value in branch.items():
            if key in _ANNOTATIONS:
                if key in result and result[key] != value:
                    return None
                result[key] = value
            elif key in result and result[key] != value:
                return None
            else:
                result[key] = value

    if len(typed_branches) == 1:
        if "enum" in result:
            values = result["enum"]
            if not isinstance(values, list):
                return None
            if None not in values:
                result["enum"] = [*values, None]
        elif "const" in result:
            result["enum"] = [result.pop("const"), None]

    result["type"] = [*types, "null"]
    return result


def normalize_nullable_types(value: Any) -> int:
    """Rewrite only equivalent anyOf null unions, returning the change count.

    Simple nullable types become OpenAPI 3.1 JSON Schema type arrays. Nullable
    references, duplicate types, and unions with distinct branch constraints
    remain anyOf because flattening them could alter validation semantics.
    """

    changed = 0
    if isinstance(value, dict):
        for child in list(value.values()):
            changed += normalize_nullable_types(child)
        normalized = _nullable_type_array(value)
        if normalized is not None:
            value.clear()
            value.update(normalized)
            changed += 1
    elif isinstance(value, list):
        for child in value:
            changed += normalize_nullable_types(child)
    return changed
