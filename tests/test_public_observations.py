"""API #54 bounded observation contract and repository boundary."""

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_observations import SnowflakeObservationRepository


def row(fips: str, value: str | None, state: str, measure: str = "case_count_floor_2023") -> tuple:
    return (
        f"observation-{fips}",
        measure,
        fips,
        "COUNTY",
        date(2023, 1, 1),
        date(2023, 12, 31),
        "YEAR",
        value,
        state,
        "cases",
        None,
        None,
        "1.0.0",
        "release-1",
        "human",
        "Human surveillance",
        "2023",
        "https://example.org/source",
        datetime(2026, 9, 7, tzinfo=UTC),
        "transform-v1",
        "count method",
        "release-method-v1",
        "Observation limit",
        "Measure limit",
        "Release limit",
    )


class FixtureRepository:
    release = "release-1"

    def current_release(self) -> str:
        return self.release

    def measure_exists(self, measure_id: str, release: str) -> bool:
        assert release == self.release
        return measure_id in {"case_count_floor_2023", "human_status"}

    def query(self, query, release: str, offset: int) -> list[tuple]:
        assert release == self.release
        rows = [
            row("01001", None, "MISSING"),
            row("01003", "0", "ZERO"),
            row("01005", "12", "OBSERVED"),
        ]
        if query.measure_id == "human_status":
            rows = [
                row("01001", '"no_county_linked_record"', "NO_COUNTY_LINKED_RECORD", "human_status")
            ]
        if query.year is not None and query.year != 2023:
            return []
        if query.start_date is not None and (
            query.start_date > date(2023, 1, 1) or query.end_date < date(2023, 12, 31)
        ):
            return []
        rows = [item for item in rows if item[2] in query.geography_id]
        return rows[offset : offset + query.page_size + 1]


def client(repository=None) -> TestClient:
    return TestClient(
        create_app(settings=ApiSettings(), observation_repository=repository or FixtureRepository())
    )


def params(**extra):
    return {
        "measure_id": "case_count_floor_2023",
        "geography_type": "county",
        "geography_id": ["01005", "01001", "01003"],
        "year": 2023,
        **extra,
    }


def test_county_period_state_and_deterministic_pagination() -> None:
    repository = FixtureRepository()
    api = client(repository)
    first = api.get("/v1/observations", params=params(page_size=2))
    assert first.status_code == 200
    assert [item["geography"]["geography_id"] for item in first.json()["data"]] == [
        "01001",
        "01003",
    ]
    missing, zero = first.json()["data"]
    assert (missing["value"], missing["value_state"]) == (None, "MISSING")
    assert (zero["value"], zero["value_state"]) == (0, "ZERO")
    assert missing["denominator"] is None
    assert missing["semantic_version"] == "1.0.0"
    assert missing["atlas_acquired_at"] is not None
    assert missing["limitations"] == ["Observation limit", "Measure limit", "Release limit"]
    token = first.json()["meta"]["next_page_token"]
    second = api.get("/v1/observations", params=params(page_size=2, page_token=token))
    assert [item["geography"]["geography_id"] for item in second.json()["data"]] == ["01005"]
    assert second.json()["meta"]["next_page_token"] is None
    repeated = api.get("/v1/observations", params=params(page_size=2, page_token=token))
    assert repeated.json()["data"] == second.json()["data"]
    assert (
        api.get(
            "/v1/observations",
            params={**params(page_size=2, page_token=token), "geography_id": "01001"},
        ).json()["code"]
        == "INVALID_REQUEST"
    )
    repository.release = "release-2"
    assert (
        api.get("/v1/observations", params=params(page_size=2, page_token=token)).json()["code"]
        == "INVALID_REQUEST"
    )
    assert (
        api.get("/v1/observations", params=params(page_token="%%%")).json()["code"]
        == "INVALID_REQUEST"
    )


def test_time_county_measure_and_strata_filters() -> None:
    api = client()
    annual_params = params()
    annual_params.pop("year")
    annual = api.get(
        "/v1/observations",
        params={**annual_params, "start_date": "2023-01-01", "end_date": "2023-12-31"},
    )
    assert annual.status_code == 200
    assert len(annual.json()["data"]) == 3
    partial = api.get(
        "/v1/observations",
        params={**annual_params, "start_date": "2023-06-01", "end_date": "2023-12-31"},
    )
    assert partial.json()["data"] == []
    assert api.get("/v1/observations", params={**params(), "year": 2024}).json()["data"] == []
    status = api.get("/v1/observations", params={**params(), "measure_id": "human_status"})
    assert status.json()["data"][0]["value_state"] == "NO_COUNTY_LINKED_RECORD"
    assert status.json()["data"][0]["value"] == "no_county_linked_record"
    cases = [
        ("measure_id", "unknown", 404, "RESOURCE_NOT_FOUND"),
        ("geography_type", "state", 400, "UNSUPPORTED_FILTER"),
        ("geography_id", "bad", 400, "INVALID_REQUEST"),
        ("stratification", "age:adult", 400, "UNSUPPORTED_STRATIFICATION"),
        ("sql", "SELECT *", 400, "UNSUPPORTED_FILTER"),
    ]
    for key, value, status_code, code in cases:
        response = api.get("/v1/observations", params={**params(), key: value})
        assert response.status_code == status_code
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == code


def test_repository_only_queries_projection_with_bound_parameters(monkeypatch) -> None:
    statements = []
    bindings = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, parameters=None):
            statements.append(statement)
            bindings.append(parameters)

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        "lyme_gap_atlas_api.public_observations.connect", lambda _settings: Connection()
    )
    repository = SnowflakeObservationRepository(ApiSettings())
    from lyme_gap_atlas_api.public_contract import ObservationQuery

    query = ObservationQuery(**params(page_size=2))
    repository.query(query, "release-1", 0)
    assert len(statements) == 1
    assert "CURRENT_COUNTY_OBSERVATIONS_V" in statements[0]
    assert "SEMANTIC_OBSERVATIONS" not in statements[0]
    assert "ORDER BY MEASURE_ID, COUNTY_FIPS, PERIOD_START, OBSERVATION_ID" in statements[0]
    assert "LIMIT %s OFFSET %s" in statements[0]
    assert bindings[0][0] == "case_count_floor_2023"
    assert bindings[0][-2:] == (3, 0)
    assert "01001" not in statements[0]
