"""Standard FastAPI documentation helpers with approved Atlas assets."""

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, Response

router = APIRouter(include_in_schema=False)
ASSETS = Path(__file__).with_name("docs_assets")
ASSET_VERSION = "summit-compass-north-20261010"
FAVICON = ASSETS / "favicon.ico"
FAVICON_URL = f"/docs/{ASSET_VERSION}/favicon.ico"
LOGO_URL = f"/docs/{ASSET_VERSION}/favicon-256x256.png"
LOGO = {
    "url": f"https://api.onehealthatlas.org{LOGO_URL}",
    "altText": "Atlas",
}
SWAGGER_PARAMETERS = {
    "filter": True,
    "deepLinking": True,
    "displayRequestDuration": True,
    "defaultModelsExpandDepth": 1,
    "defaultModelExpandDepth": 1,
    "docExpansion": "list",
    # Explicit approved GETs; preserve the usual Try It Out interaction.
    "supportedSubmitMethods": ["get"],
}


@router.get(FAVICON_URL)
def favicon() -> Response:
    return Response(
        content=FAVICON.read_bytes(),
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get(LOGO_URL)
def logo() -> Response:
    return Response(
        content=(ASSETS / "favicon-256x256.png").read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def install_documentation(app: FastAPI) -> None:
    """Keep framework renderers, asset defaults and security middleware intact."""
    app.include_router(router)

    @app.get("/docs", include_in_schema=False)
    def swagger() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} — Swagger",
            swagger_favicon_url=FAVICON_URL,
            swagger_ui_parameters=SWAGGER_PARAMETERS,
        )

    @app.get("/redoc", include_in_schema=False)
    def redoc() -> HTMLResponse:
        return get_redoc_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} — ReDoc",
            redoc_favicon_url=FAVICON_URL,
        )
