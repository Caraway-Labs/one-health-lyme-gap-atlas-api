"""API #55 source/method resolution and governed null semantics."""

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_provenance import SnowflakeProvenanceRepository

HUMAN = (
    "human",
    "CDC Lyme surveillance",
    "2023",
    "https://data.cdc.gov/resource/x5j9-wybp.json",
    "Residence, not exposure; privacy-protected floor.",
    "cdc_lyme",
    "x5j9-wybp",
    "Centers for Disease Control and Prevention",
    None,
    None,
    "1.0.0",
    "release-1",
)
OTHER = (
    "tick",
    "Tick source",
    "2023",
    None,
    "Source caveat.",
    "cdc",
    "tick-dataset",
    None,
    None,
    None,
    "1.0.0",
    "release-1",
)
METHOD = (
    "human_confirmed_probable_case_floor_v1",
    "case_count_floor_2023",
    "x5j9 confirmed plus probable",
    "semantic-1.0.0",
    "Privacy-protected floor.",
    "1.0.0",
    "release-1",
)


class FixtureRepository:
    release = "release-1"

    def current_release(self) -> str:
        return self.release

    def sources(self, release: str, limit: int, offset: int) -> list[tuple]:
        assert release == self.release
        return [HUMAN, OTHER][offset : offset + limit]

    def source(self, source_id: str, release: str) -> tuple | None:
        assert release == self.release
        return {"human": HUMAN, "tick": OTHER}.get(source_id)

    def methodology(self, methodology_id: str, release: str) -> tuple | None:
        assert release == self.release
        return METHOD if methodology_id == METHOD[0] else None


def test_sources_methods_and_unknowns() -> None:
    repository = FixtureRepository()
    api = TestClient(create_app(settings=ApiSettings(), provenance_repository=repository))
    first = api.get("/v1/sources", params={"page_size": 1})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=60, must-revalidate"
    source = first.json()["data"][0]
    assert (source["source_id"], source["lineage_source_id"], source["dataset_id"]) == (
        "human",
        "cdc_lyme",
        "x5j9-wybp",
    )
    assert source["upstream_updated_at"] is None
    assert source["source_retrieved_at"] is None
    assert source["source_vintage"] == "2023"
    assert source["source_version"] is None
    assert source["limitations"] == [HUMAN[4]]
    token = first.json()["meta"]["next_page_token"]
    second = api.get("/v1/sources", params={"page_size": 1, "page_token": token})
    assert second.json()["data"][0]["publisher"] is None
    assert second.json()["meta"]["next_page_token"] is None
    assert api.get("/v1/sources/human").json()["data"] == source
    method = api.get(f"/v1/methodologies/{METHOD[0]}").json()["data"]
    assert method["measure_id"] == "case_count_floor_2023"
    assert method["version"] == "semantic-1.0.0"
    assert method["limitations"] == ["Privacy-protected floor."]
    for path in ("/v1/sources/missing", "/v1/methodologies/missing"):
        response = api.get(path)
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert api.get("/v1/sources", params={"arbitrary": "x"}).json()["code"] == "UNSUPPORTED_FILTER"
    assert api.get("/v1/sources", params={"page_token": "%%%"}).json()["code"] == "INVALID_REQUEST"
    repository.release = "release-2"
    assert api.get("/v1/sources", params={"page_token": token}).json()["code"] == "INVALID_REQUEST"


def test_repository_queries_only_public_views_with_bound_identifiers(monkeypatch) -> None:
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params=()):
            calls.append((statement, params))

        def fetchone(self):
            return None

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
        "lyme_gap_atlas_api.public_provenance.connect", lambda _settings: Connection()
    )
    repository = SnowflakeProvenanceRepository(ApiSettings())
    repository.sources("release", 2, 0)
    repository.source("human", "release")
    repository.methodology(METHOD[0], "release")
    assert all(
        "CURRENT_" in statement and "SEMANTIC_DATA_SOURCES" not in statement
        for statement, _ in calls
    )
    assert calls[0][1] == ("release", 2, 0)
    assert calls[1][1] == ("human", "release")
    assert calls[2][1] == (METHOD[0], "release")
    assert all("human" not in statement for statement, _ in calls)
