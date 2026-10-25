"""Documented success headers match what the routes send."""

from typing import Any

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_docs import RESPONSE_HEADERS


def _operations() -> dict[str, dict[str, Any]]:
    schema = TestClient(create_app(settings=ApiSettings())).get("/openapi.json").json()
    return {
        operation["operationId"]: operation
        for path_item in schema["paths"].values()
        for operation in path_item.values()
    }


def test_every_public_success_response_documents_its_headers() -> None:
    operations = _operations()
    assert set(operations) == set(RESPONSE_HEADERS)
    for operation_id, operation in operations.items():
        headers = operation["responses"]["200"]["headers"]
        assert {"Content-Type", "X-Request-ID"} <= headers.keys(), operation_id
        spec = RESPONSE_HEADERS[operation_id]
        assert ("ETag" in headers) is spec.etag, operation_id
        assert ("Content-Disposition" in headers) is spec.attachment, operation_id
        if spec.cache is not None:
            assert spec.cache in headers["Cache-Control"]["description"], operation_id


def test_conditional_support_is_stated_for_every_operation() -> None:
    for operation_id, operation in _operations().items():
        responses = operation["responses"]
        if RESPONSE_HEADERS[operation_id].conditional:
            assert "304" in responses, operation_id
            assert "If-None-Match" in responses["304"]["description"], operation_id
        else:
            assert "304" not in responses, operation_id
            assert "ignores `If-None-Match`" in responses["200"]["description"], operation_id


def test_rate_limit_and_retry_guidance_is_in_the_overview() -> None:
    client = TestClient(create_app(settings=ApiSettings()))
    description = client.get("/openapi.json").json()["info"]["description"]
    assert "## Rate limits and retries" in description
    assert "`Retry-After`" in description
    assert "60 requests in a rolling minute" in description
