"""Durable machine/framework documentation URLs and consumer separation."""

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.documentation import FAVICON, LOGO, SWAGGER_PARAMETERS


def test_canonical_documentation_urls_and_cross_link() -> None:
    client = TestClient(create_app(settings=ApiSettings()))
    response = client.get("/openapi.json", follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    schema = response.json()
    assert schema["externalDocs"] == {
        "description": "Atlas documentation and developer guides",
        "url": "https://carawaylabs.com/docs",
    }
    assert schema["servers"] == [
        {"url": "https://api.carawaylabs.com", "description": "Production"}
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
