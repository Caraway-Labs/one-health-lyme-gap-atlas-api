"""The geography identity route answers its unpublished state as a typed 200 (#343, ADR 0021)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings

IDENTITY = "/v1/geographies/{geography_type}/{geography_id}"


class ForbiddenRepository:
    """Any directory or snapshot access fails the test: the identity route must not need one."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"identity route touched the repository: {name}")


def client(**settings: Any) -> TestClient:
    return TestClient(
        create_app(repository=ForbiddenRepository(), settings=ApiSettings(**settings)),  # type: ignore[arg-type]
        raise_server_exceptions=True,
    )


@pytest.mark.parametrize(
    ("geography_type", "geography_id"),
    [("county", "01001"), ("county", "99999"), ("state", "01"), ("state", "99")],
)
def test_valid_shape_is_answered_without_touching_snowflake_or_the_directory(
    geography_type: str, geography_id: str
) -> None:
    response = client().get(f"/v1/geographies/{geography_type}/{geography_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["availability"] == "UNAVAILABLE"
    assert body["geography"] == {"geography_type": geography_type, "geography_id": geography_id}
    # Shape is validated, membership is deliberately not asserted for this route.
    assert any("shape only" in item for item in body["limitations"])


@pytest.mark.parametrize(
    "path",
    [
        "/v1/geographies/county/123",
        "/v1/geographies/county/abcde",
        "/v1/geographies/state/001",
        "/v1/geographies/state/1a",
        "/v1/geographies/region/01",
    ],
)
def test_invalid_identities_keep_their_400_problem(path: str) -> None:
    response = client().get(path)
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "INVALID_REQUEST"


def test_general_rate_limit_still_applies_with_a_real_429() -> None:
    """The identity route sits under the API-wide limiter (not the public-collection one)."""
    api = client(rate_limit_per_minute=10)
    statuses = [api.get("/v1/geographies/county/01001").status_code for _ in range(11)]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429
    response = api.get("/v1/geographies/county/01001")
    assert response.status_code == 429
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"]


def test_no_5xx_is_produced_and_responses_are_not_cacheable() -> None:
    api = client()
    for path in ("/v1/geographies/county/01001", "/v1/geographies/state/01"):
        response = api.get(path)
        assert response.status_code < 500
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-request-id"]
