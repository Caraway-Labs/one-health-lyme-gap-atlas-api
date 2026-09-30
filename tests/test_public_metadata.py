"""API #53 governed metadata discovery contract."""

import base64
import json

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_metadata import MetadataRows, SnowflakeMetadataRepository


class MetadataFixture:
    def load_metadata(self) -> MetadataRows:
        return MetadataRows(
            indicators=[
                ("alpha", "Alpha", None, "Limited", None, None, "1.0.0", "release-1"),
                ("beta", "Beta", "Description", None, None, None, "1.0.0", "release-1"),
            ],
            measures=[
                (
                    "a",
                    "alpha",
                    "A",
                    None,
                    "NUMBER",
                    "cases",
                    None,
                    "COUNTY_FIPS_5",
                    "2023",
                    None,
                    None,
                    None,
                    "unknown",
                    "method",
                    "Measure limit",
                    "1.0.0",
                    "release-1",
                ),
                (
                    "b",
                    "alpha",
                    "B",
                    None,
                    "NUMBER",
                    "records",
                    None,
                    "STATE",
                    "snapshot",
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "1.0.0",
                    "release-1",
                ),
            ],
        )


def test_metadata_discovery_filters_pagination_and_nulls() -> None:
    client = TestClient(create_app(settings=ApiSettings(), metadata_repository=MetadataFixture()))
    first = client.get("/v1/indicators", params={"page_size": 1})
    assert first.status_code == 200
    assert first.json()["data"][0]["measure_ids"] == ["a", "b"]
    assert first.json()["data"][0]["definition"] is None
    assert first.json()["data"][0]["release_version"] == "release-1"
    indicator = client.get("/v1/indicators/alpha")
    assert indicator.status_code == 200
    assert indicator.json()["data"] == first.json()["data"][0]
    token = first.json()["meta"]["next_page_token"]
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    payload = json.loads(raw[:-32])
    payload["offset"] = 0
    tampered = base64.urlsafe_b64encode(json.dumps(payload).encode() + raw[-32:]).decode()
    assert (
        client.get("/v1/indicators", params={"page_token": tampered}).json()["code"]
        == "INVALID_REQUEST"
    )
    assert (
        client.get("/v1/indicators", params={"page_size": 1, "page_token": token}).json()["data"][
            0
        ]["indicator_id"]
        == "beta"
    )
    assert (
        client.get("/v1/indicators", params={"indicator_id": "alpha", "page_token": token}).json()[
            "code"
        ]
        == "INVALID_REQUEST"
    )
    assert (
        client.get("/v1/measures", params={"geography_type": "STATE"}).json()["data"][0][
            "measure_id"
        ]
        == "b"
    )
    measure = client.get("/v1/measures/a").json()["data"]
    assert measure["geography_semantics"] == "COUNTY_FIPS_5"
    assert measure["geography_types"] is None
    assert measure["denominator"] is None
    assert measure["source_ids"] is None
    assert measure["methodology"] == "method"
    assert client.get("/v1/measures/missing").json()["code"] == "RESOURCE_NOT_FOUND"
    assert client.get("/v1/indicators/missing").json()["code"] == "RESOURCE_NOT_FOUND"
    for path in ("/v1/indicators", "/v1/measures"):
        unsupported = client.get(path, params={"availability": "true"})
        assert unsupported.status_code == 400
        assert unsupported.json()["code"] == "UNSUPPORTED_FILTER"


def test_repository_reads_only_governed_metadata_views(monkeypatch) -> None:
    statements = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, *, timeout=None):
            assert timeout == 15
            statements.append(statement)

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
        "lyme_gap_atlas_api.public_metadata.connect", lambda _settings: Connection()
    )
    SnowflakeMetadataRepository(ApiSettings()).load_metadata()
    assert len(statements) == 2
    assert "CURRENT_INDICATOR_METADATA_V" in statements[0]
    assert "CURRENT_MEASURE_METADATA_V" in statements[1]
    for statement in statements:
        assert "ORDER BY" in statement
        assert "SEMANTIC_RELEASE_POINTER" not in statement
        assert "SEMANTIC_INDICATORS" not in statement
        assert "SEMANTIC_MEASURES" not in statement
