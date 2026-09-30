"""API #56 public-read protection and conditional HTTP behavior."""

import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from test_public_metadata import MetadataFixture

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings


def client(**settings: int) -> TestClient:
    return TestClient(
        create_app(
            settings=ApiSettings(rate_limit_per_minute=100, **settings),
            metadata_repository=MetadataFixture(),
        )
    )


def test_public_limit_and_retry_guidance() -> None:
    api = client(public_rate_limit_per_minute=2)
    for _ in range(2):
        assert api.get("/v1/indicators").status_code == 200
    limited = api.get("/v1/indicators")
    assert limited.status_code == 429
    assert limited.headers["content-type"] == "application/problem+json"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert limited.headers["Cache-Control"] == "no-store"
    assert limited.json()["code"] == "RATE_LIMITED"
    assert "ip" not in limited.text.lower()


def test_request_bounds_and_repeated_scalars() -> None:
    api = client(public_max_query_bytes=512)
    repeated = api.get("/v1/indicators", params=[("page_size", "1"), ("page_size", "2")])
    assert (repeated.status_code, repeated.json()["code"]) == (400, "INVALID_REQUEST")
    oversized = api.get("/v1/indicators", params={"q": "x" * 512})
    assert oversized.status_code == 414
    assert api.request("GET", "/v1/indicators", content=b"x").status_code == 413


def test_detail_etag_and_conditional_response() -> None:
    api = client()
    first = api.get("/v1/measures/a")
    assert first.status_code == 200
    assert first.headers["ETag"].startswith('"')
    assert first.headers["Cache-Control"] == "public, max-age=60, must-revalidate"
    conditional = api.get("/v1/measures/a", headers={"If-None-Match": first.headers["ETag"]})
    assert conditional.status_code == 304
    assert conditional.content == b""
    assert conditional.headers["ETag"] == first.headers["ETag"]


def test_page_size_ceiling_is_consistent() -> None:
    api = client()
    assert api.get("/v1/indicators", params={"page_size": 500}).status_code == 200
    oversized = api.get("/v1/indicators", params={"page_size": 501})
    assert (oversized.status_code, oversized.json()["code"]) == (400, "INVALID_REQUEST")


def test_concurrent_public_reads_are_bounded() -> None:
    entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()

    class SlowMetadata(MetadataFixture):
        count = 0

        def load_metadata(self):
            with lock:
                self.count += 1
                if self.count == 2:
                    entered.set()
            assert release.wait(10)
            return super().load_metadata()

    api = TestClient(
        create_app(
            settings=ApiSettings(public_concurrent_requests_per_ip=2),
            metadata_repository=SlowMetadata(),
        )
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(api.get, "/v1/indicators")
        second = pool.submit(api.get, "/v1/indicators")
        try:
            assert entered.wait(10)
            limited = api.get("/v1/indicators")
            assert limited.status_code == 429
            assert limited.headers["Retry-After"] == "1"
        finally:
            release.set()
        assert first.result().status_code == 200
        assert second.result().status_code == 200
