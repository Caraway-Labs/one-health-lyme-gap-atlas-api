"""Guard API serialization and pagination overhead without a warehouse dependency."""

from concurrent.futures import ThreadPoolExecutor
from statistics import median
from time import perf_counter

from fastapi.testclient import TestClient
from test_public_observations import row

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings


class SizedObservations:
    def current_release(self) -> str:
        return "release-1"

    def measure_exists(self, measure_id: str, release: str) -> bool:
        return measure_id == "case_count_floor_2023" and release == "release-1"

    def query(self, query, release: str, offset: int) -> list[tuple]:
        assert release == "release-1"
        assert query.year == 2023
        rows = [row(fips, "1", "OBSERVED") for fips in sorted(query.geography_id)]
        return rows[offset : offset + query.page_size + 1]


def test_common_and_large_allowed_observation_shapes() -> None:
    api = TestClient(create_app(settings=ApiSettings(), observation_repository=SizedObservations()))
    samples = []
    for count in (1, 400):
        ids = [f"{i:05d}" for i in range(1001, 1001 + count)]
        params = [
            ("measure_id", "case_count_floor_2023"),
            ("geography_type", "county"),
            ("year", "2023"),
            ("page_size", "500"),
            *(("geography_id", fips) for fips in ids),
        ]
        durations = []
        for _ in range(3):
            start = perf_counter()
            response = api.get("/v1/observations", params=params)
            durations.append(perf_counter() - start)
            assert response.status_code == 200
            assert len(response.json()["data"]) == count
            assert response.json()["meta"]["next_page_token"] is None
        samples.append(median(durations))
    # Generous CI guard for application overhead; live warehouse latency is
    # measured separately by the bounded production probe.
    assert samples[0] < 1.0
    assert samples[1] < 3.0


def test_allowed_five_request_burst_completes() -> None:
    api = TestClient(create_app(settings=ApiSettings(), observation_repository=SizedObservations()))
    params = {
        "measure_id": "case_count_floor_2023",
        "geography_type": "county",
        "geography_id": "01001",
        "year": 2023,
    }
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: api.get("/v1/observations", params=params), range(5)))
    assert [response.status_code for response in responses] == [200] * 5
    assert perf_counter() - started < 5.0
