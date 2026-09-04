"""Renderer contracts and limits kept independent from FastAPI routes."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ..config import ApiSettings
from .models import CountyReport, StateReport

Report = CountyReport | StateReport


class RendererError(Exception):
    """Safe base error for renderer failures that routes may translate later."""


class UnknownTemplateError(RendererError):
    pass


class ResourceLimitExceeded(RendererError):
    pass


class RenderTimeout(RendererError):
    pass


class RenderCompilationError(RendererError):
    pass


class RendererFailure(RendererError):
    pass


@dataclass(frozen=True)
class RenderLimits:
    timeout_seconds: float
    max_pages: int
    max_report_items: int
    max_individual_asset_bytes: int
    max_aggregate_asset_bytes: int
    max_pdf_bytes: int

    @classmethod
    def from_settings(cls, settings: ApiSettings) -> "RenderLimits":
        return cls(
            timeout_seconds=settings.pdf_render_timeout_seconds,
            max_pages=settings.pdf_max_pages,
            max_report_items=settings.pdf_max_report_items,
            max_individual_asset_bytes=settings.pdf_max_individual_asset_bytes,
            max_aggregate_asset_bytes=settings.pdf_max_aggregate_asset_bytes,
            max_pdf_bytes=settings.pdf_max_pdf_bytes,
        )


class PdfRenderer(Protocol):
    def render(self, report: Report, template_key: str) -> bytes: ...


def validate_asset_sizes(assets: Sequence[bytes], limits: RenderLimits) -> None:
    """Extension point for future chart/map assets before they reach Typst."""

    total = 0
    for asset in assets:
        size = len(asset)
        if size > limits.max_individual_asset_bytes:
            raise ResourceLimitExceeded("A report asset exceeds the configured size limit.")
        total += size
    if total > limits.max_aggregate_asset_bytes:
        raise ResourceLimitExceeded("Report assets exceed the configured aggregate size limit.")
