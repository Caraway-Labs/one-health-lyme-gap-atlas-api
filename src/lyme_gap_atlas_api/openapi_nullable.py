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
_SCHEMA_MAP_KEYWORDS = {
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
}
_SCHEMA_SINGLE_KEYWORDS = {
    "additionalItems",
    "additionalProperties",
    "contentSchema",
    "contains",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
}
_SCHEMA_ARRAY_KEYWORDS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_OPAQUE_KEYWORDS = {"default", "enum", "example", "examples", "const"}


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
        branch for branch in branches if isinstance(branch, dict) and branch.get("type") != "null"
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


def _visit_schema(schema: Any) -> int:
    if not isinstance(schema, dict):
        return 0
    changed = 0
    for key, child in list(schema.items()):
        if key in _OPAQUE_KEYWORDS:
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(child, dict):
            changed += sum(_visit_schema(item) for item in child.values())
        elif key in _SCHEMA_SINGLE_KEYWORDS:
            if isinstance(child, list):
                changed += sum(_visit_schema(item) for item in child)
            else:
                changed += _visit_schema(child)
        elif key in _SCHEMA_ARRAY_KEYWORDS and isinstance(child, list):
            changed += sum(_visit_schema(item) for item in child)
    normalized = _nullable_type_array(schema)
    if normalized is not None:
        schema.clear()
        schema.update(normalized)
        changed += 1
    return changed


def _visit_media_type(media_type: Any) -> int:
    if not isinstance(media_type, dict):
        return 0
    return _visit_schema(media_type.get("schema"))


def _visit_content(content: Any) -> int:
    if not isinstance(content, dict):
        return 0
    return sum(_visit_media_type(media_type) for media_type in content.values())


def _visit_parameter(parameter: Any) -> int:
    if not isinstance(parameter, dict):
        return 0
    changed = _visit_schema(parameter.get("schema"))
    changed += _visit_content(parameter.get("content"))
    return changed


def _visit_header(header: Any) -> int:
    return _visit_parameter(header)


def _visit_request_body(request_body: Any) -> int:
    if not isinstance(request_body, dict):
        return 0
    return _visit_content(request_body.get("content"))


def _visit_response(response: Any) -> int:
    if not isinstance(response, dict):
        return 0
    changed = _visit_content(response.get("content"))
    headers = response.get("headers")
    if isinstance(headers, dict):
        changed += sum(_visit_header(header) for header in headers.values())
    # Response links are runtime expressions/examples, not schema locations.
    return changed


def _visit_callback(callback: Any) -> int:
    if not isinstance(callback, dict):
        return 0
    return sum(
        _visit_path_item(path_item)
        for name, path_item in callback.items()
        if not name.startswith("x-")
    )


def _visit_operation(operation: Any) -> int:
    if not isinstance(operation, dict):
        return 0
    changed = 0
    parameters = operation.get("parameters")
    if isinstance(parameters, list):
        changed += sum(_visit_parameter(parameter) for parameter in parameters)
    changed += _visit_request_body(operation.get("requestBody"))
    responses = operation.get("responses")
    if isinstance(responses, dict):
        changed += sum(
            _visit_response(response)
            for status_code, response in responses.items()
            if not status_code.startswith("x-")
        )
    callbacks = operation.get("callbacks")
    if isinstance(callbacks, dict):
        changed += sum(_visit_callback(callback) for callback in callbacks.values())
    return changed


def _visit_path_item(path_item: Any) -> int:
    if not isinstance(path_item, dict):
        return 0
    changed = 0
    parameters = path_item.get("parameters")
    if isinstance(parameters, list):
        changed += sum(_visit_parameter(parameter) for parameter in parameters)
    methods = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    changed += sum(
        _visit_operation(operation) for method, operation in path_item.items() if method in methods
    )
    return changed


def _visit_openapi(document: Any) -> int:
    if not isinstance(document, dict):
        return 0
    changed = 0
    for root_key in ("paths", "webhooks"):
        path_items = document.get(root_key)
        if isinstance(path_items, dict):
            changed += sum(
                _visit_path_item(path_item)
                for name, path_item in path_items.items()
                if not name.startswith("x-")
            )
    components = document.get("components")
    if isinstance(components, dict):
        schemas = components.get("schemas")
        if isinstance(schemas, dict):
            changed += sum(_visit_schema(schema) for schema in schemas.values())
        for key in ("parameters", "headers", "responses", "requestBodies"):
            entries = components.get(key)
            if isinstance(entries, dict):
                visitor = {
                    "parameters": _visit_parameter,
                    "headers": _visit_header,
                    "responses": _visit_response,
                    "requestBodies": _visit_request_body,
                }[key]
                changed += sum(visitor(entry) for entry in entries.values())
        callbacks = components.get("callbacks")
        if isinstance(callbacks, dict):
            changed += sum(_visit_callback(callback) for callback in callbacks.values())
        path_items = components.get("pathItems")
        if isinstance(path_items, dict):
            changed += sum(_visit_path_item(path_item) for path_item in path_items.values())
    return changed


def normalize_nullable_types(value: Any) -> int:
    """Rewrite equivalent OpenAPI schema unions, ignoring literal payload data.

    Simple nullable types become OpenAPI 3.1 JSON Schema type arrays. Nullable
    references, duplicate types, and unions with distinct branch constraints
    remain anyOf because flattening them could alter validation semantics. Only
    explicit OpenAPI Schema Object locations are visited; Link Objects and
    literal enum/default/example/extension values are left untouched.
    """
    if isinstance(value, dict) and any(key in value for key in ("openapi", "components", "paths")):
        return _visit_openapi(value)
    return _visit_schema(value)
