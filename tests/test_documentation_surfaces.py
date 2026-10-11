"""Durable machine/framework documentation URLs and consumer separation."""

import hashlib
import re
import struct
from pathlib import Path

from fastapi.testclient import TestClient
from test_public_metadata import MetadataFixture

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.documentation import ASSETS, FAVICON_URL, LOGO, LOGO_URL, SWAGGER_PARAMETERS


def test_legacy_swagger_can_call_the_canonical_server_with_manifest_cors() -> None:
    manifest = (Path(__file__).resolve().parents[1] / ".do/app.yaml").read_text()
    match = re.search(r"key: CORS_ORIGINS\s+value: ([^\n]+)", manifest)
    assert match is not None
    settings = ApiSettings(cors_origins=match.group(1).strip().split(","))
    client = TestClient(create_app(settings=settings, metadata_repository=MetadataFixture()))
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
    assert (
        "access-control-allow-origin"
        not in client.get("/openapi.json", headers={"Origin": "https://untrusted.example"}).headers
    )


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
        assert f'href="{FAVICON_URL}"' in page.text
        assert "favicon.svg" not in page.text
    swagger = client.get("/docs").text
    assert "SwaggerUIBundle" in swagger
    for key, value in SWAGGER_PARAMETERS.items():
        import json

        assert f'"{key}": {json.dumps(value)}' in swagger
    assert LOGO["url"] in schema["info"]["description"]
    assert "favicon.svg" not in str(schema["info"])
    assert client.get("/docs/favicon.svg").status_code == 404
    for url, mime, checksum in (
        (
            FAVICON_URL,
            "image/x-icon",
            "9a3f9ae0df3e926a33c2e8dbbabcf1c1b4770515ed75a7c673ad8304d7c98d19",
        ),
        (LOGO_URL, "image/png", "b803ae4b899ff9a318bba31a049ee3ecd1d65fd0b80530da5b9361aa457eac23"),
    ):
        asset = client.get(url)
        assert asset.status_code == 200
        assert asset.headers["content-type"] == mime
        assert asset.headers["cache-control"] == "public, max-age=86400"
        assert asset.content == (ASSETS / url.rsplit("/", 1)[1]).read_bytes()
        assert hashlib.sha256(asset.content).hexdigest() == checksum
        assert client.get(url, headers={"Cache-Control": "no-cache"}).content == asset.content
        assert url not in schema["paths"]
        assert url not in client.app.first_party_openapi()["paths"]


def test_approved_icon_dimensions() -> None:
    ico = (ASSETS / "favicon.ico").read_bytes()
    assert struct.unpack_from("<HHH", ico) == (0, 1, 3)
    assert {(ico[6 + i * 16], ico[7 + i * 16]) for i in range(3)} == {(16, 16), (32, 32), (48, 48)}
    png = (ASSETS / "favicon-256x256.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack_from(">II", png, 16) == (256, 256)
