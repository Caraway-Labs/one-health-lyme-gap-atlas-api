"""Durable machine/framework documentation URLs and consumer separation."""

from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings


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
    assert len(schema["paths"]) == 16
    for path in ("/docs", "/redoc"):
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "/openapi.json" in page.text
        assert "first-party-openapi.json" not in page.text
    assert client.get("/first-party-openapi.json").status_code == 404
