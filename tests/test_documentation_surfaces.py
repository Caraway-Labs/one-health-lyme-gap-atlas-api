"""Durable machine/framework documentation URLs and consumer separation."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.documentation import FAVICON, LOGO, SWAGGER_PARAMETERS
from lyme_gap_atlas_api.public_metadata import MetadataRows


class DocumentationMetadata:
    def load_metadata(self) -> MetadataRows:
        return MetadataRows(indicators=[], measures=[])


def test_legacy_swagger_can_call_the_canonical_server_with_manifest_cors() -> None:
    manifest = (Path(__file__).resolve().parents[1] / ".do/app.yaml").read_text()
    match = re.search(r"key: CORS_ORIGINS\s+value: ([^\n]+)", manifest)
    assert match is not None
    settings = ApiSettings(cors_origins=match.group(1).strip().split(","))
    client = TestClient(create_app(settings=settings, metadata_repository=DocumentationMetadata()))
    for origin in ("https://api.carawaylabs.com", "https://onehealthatlas.org"):
        headers = {"Origin": origin}
        response = client.get("/openapi.json", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        preflight = client.options(
            "/v1/indicators",
            headers={**headers, "Access-Control-Request-Method": "GET"},
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        result = client.get("/v1/indicators", headers=headers)
        assert result.status_code == 200
        assert result.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-origin" not in client.get(
        "/openapi.json", headers={"Origin": "https://untrusted.example"}
    ).headers


def test_canonical_documentation_urls_and_cross_link() -> None:
    client = TestClient(create_app(settings=ApiSettings()))
    response = client.get("/openapi.json", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    schema = response.json()
    assert schema["externalDocs"] == {
        "description": "Atlas documentation and developer guides",
        "url": "https://onehealthatlas.org/docs",
    }
    assert schema["servers"] == [
        {"url": "https://api.onehealthatlas.org", "description": "Production"}
    ]
    assert len(schema["paths"]) == 18
    for path in ("/docs", "/redoc"):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "/openapi.json" in page.text
        assert "first-party-openapi.json" not in page.text
    assert client.get("/first-party-openapi.json").status_code == 404


def test_atlas_branding_and_standard_swagger_interaction() -> None:
    client = TestClient(create_app(settings=ApiSettings()))
    schema = client.get("/openapi.json").json()
    assert schema["info"]["x-logo"] == LOGO
    for path in ("/docs", "/redoc"):
        page = client.get(path)
        assert "One Health Lyme Gap Atlas API" in page.text
        assert 'href="/docs/favicon.svg"' in page.text
    swagger = client.get("/docs").text
    assert "SwaggerUIBundle" in swagger
    for key, value in SWAGGER_PARAMETERS.items():
        import json

        assert f'"{key}": {json.dumps(value)}' in swagger
    asset = client.get("/docs/favicon.svg")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("image/svg+xml")
    assert asset.content == FAVICON.read_bytes()
    assert "/docs/favicon.svg" not in schema["paths"]
    assert "/docs/favicon.svg" not in client.app.first_party_openapi()["paths"]
