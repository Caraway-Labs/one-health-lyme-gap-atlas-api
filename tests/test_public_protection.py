"""API #56 public-read protection and conditional HTTP behavior."""

import asyncio

import anyio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
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
    async def run() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowMetadata(MetadataFixture):
            count = 0

            def mark_entered(self):
                self.count += 1
                if self.count == 2:
                    entered.set()

            def load_metadata(self):
                # Coordinate on the ASGI loop; worker-local wall timers could
                # release occupied slots before the rejection assertion ran.
                anyio.from_thread.run_sync(self.mark_entered)
                anyio.from_thread.run(release.wait)
                return super().load_metadata()

        metadata = SlowMetadata()
        app = create_app(
            settings=ApiSettings(public_concurrent_requests_per_ip=2),
            metadata_repository=metadata,
        )
        async with AsyncClient(transport=ASGITransport(app), base_url="http://fixture") as api:
            async with asyncio.timeout(10):
                first = asyncio.create_task(api.get("/v1/indicators"))
                second = asyncio.create_task(api.get("/v1/indicators"))
                try:
                    await entered.wait()
                    limited = await api.get("/v1/indicators")
                    assert limited.status_code == 429
                    assert limited.headers["Retry-After"] == "1"
                    assert metadata.count == 2
                finally:
                    release.set()
                    responses = await asyncio.gather(first, second)
                assert all(response.status_code == 200 for response in responses)
                assert metadata.count == 2
                assert (await api.get("/v1/indicators")).status_code == 200
                assert metadata.count == 3

    asyncio.run(run())
