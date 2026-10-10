"""Standard FastAPI documentation helpers with approved Atlas assets."""

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, Response

router = APIRouter(include_in_schema=False)
FAVICON = Path(__file__).with_name("docs_assets") / "favicon.svg"
LOGO = {
    "url": "https://api.onehealthatlas.org/docs/favicon.svg",
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


@router.get("/docs/favicon.svg")
def favicon() -> Response:
    return Response(
        content=FAVICON.read_bytes(),
        media_type="image/svg+xml",
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
            swagger_favicon_url="/docs/favicon.svg",
            swagger_ui_parameters=SWAGGER_PARAMETERS,
        )

    @app.get("/redoc", include_in_schema=False)
    def redoc() -> HTMLResponse:
        return get_redoc_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} — ReDoc",
            redoc_favicon_url="/docs/favicon.svg",
        )
