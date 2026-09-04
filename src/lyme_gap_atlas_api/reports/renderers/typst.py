"""Safe, synchronous rendering of trusted report templates with Typst."""

import json
import logging
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..models import CountyReport, StateReport
from ..renderer import (
    RenderCompilationError,
    RendererError,
    RendererFailure,
    RenderLimits,
    RenderTimeout,
    Report,
    ResourceLimitExceeded,
    UnknownTemplateError,
)
from ..template_registry import trusted_template_paths

_LOGGER = logging.getLogger(__name__)
_TRACER = trace.get_tracer(__name__)
_DEFAULT_TEMPLATES = trusted_template_paths()


class TypstRenderer:
    """Runs a pinned Typst binary against server-registered template keys only."""

    def __init__(
        self,
        limits: RenderLimits,
        template_directory: Path | None = None,
        templates: Mapping[str, str] | None = None,
        binary: Sequence[str] = ("typst",),
    ) -> None:
        self._limits = limits
        self._template_directory = template_directory or Path(__file__).parents[1] / "templates"
        self._templates = dict(templates or _DEFAULT_TEMPLATES)
        self._binary = tuple(binary)

    def render(self, report: Report, template_key: str) -> bytes:
        template = self._trusted_template(template_key)
        item_count = _item_count(report.model_dump(mode="json"))
        if item_count > self._limits.max_report_items:
            raise ResourceLimitExceeded("The report exceeds the configured item limit.")

        started = time.perf_counter()
        with _TRACER.start_as_current_span("atlas.report.render") as span:
            span.set_attribute("atlas.report.template", template_key)
            span.set_attribute("atlas.report.geography_type", report.geography.level)
            try:
                payload, page_count = self._render(template, report)
                duration_ms = (time.perf_counter() - started) * 1_000
                span.set_attribute("atlas.report.duration_ms", duration_ms)
                span.set_attribute("atlas.report.output_bytes", len(payload))
                span.set_attribute("atlas.report.page_count", page_count)
                span.set_attribute("atlas.report.outcome", "success")
                _LOGGER.info(
                    "report_rendered",
                    extra={
                        "template": template_key,
                        "geography_type": report.geography.level,
                        "duration_ms": duration_ms,
                        "output_bytes": len(payload),
                        "page_count": page_count,
                        "outcome": "success",
                    },
                )
                return payload
            except RendererError as exc:
                self._record_error(span, report, template_key, started, type(exc).__name__)
                raise
            except Exception as exc:
                self._record_error(span, report, template_key, started, "unexpected")
                raise RendererFailure("The report renderer is unavailable.") from exc

    def _render(self, template: Path, report: Report) -> tuple[bytes, int]:
        with tempfile.TemporaryDirectory(prefix="atlas-report-") as directory:
            workspace = Path(directory)
            template_copy = workspace / "template.typ"
            output = workspace / "report.pdf"
            template_root = self._template_directory.resolve()
            template_relative_path = template.relative_to(template_root)
            staged_templates = workspace / "templates"
            shutil.copytree(template_root, staged_templates)
            template_copy = staged_templates / template_relative_path
            (template_copy.parent / "input.json").write_text(
                json.dumps(report.model_dump(mode="json"), separators=(",", ":")), encoding="utf-8"
            )
            try:
                completed = subprocess.run(
                    [
                        *self._binary,
                        "compile",
                        "--root",
                        str(workspace),
                        str(template_copy),
                        str(output),
                    ],
                    cwd=workspace,
                    capture_output=True,
                    check=False,
                    timeout=self._limits.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise RenderTimeout("Report rendering exceeded the configured time limit.") from exc
            except OSError as exc:
                raise RendererFailure("The report renderer is unavailable.") from exc
            if completed.returncode != 0:
                raise RenderCompilationError("The trusted report template could not be compiled.")
            try:
                payload = output.read_bytes()
                page_count = len(PdfReader(output).pages)
            except (FileNotFoundError, PdfReadError) as exc:
                raise RendererFailure("The renderer did not produce a valid PDF.") from exc
        if len(payload) > self._limits.max_pdf_bytes:
            raise ResourceLimitExceeded("The generated report exceeds the configured size limit.")
        if page_count > self._limits.max_pages:
            raise ResourceLimitExceeded("The generated report exceeds the configured page limit.")
        return payload, page_count

    def _trusted_template(self, template_key: str) -> Path:
        filename = self._templates.get(template_key)
        if filename is None:
            raise UnknownTemplateError("The requested report template is not available.")
        root = self._template_directory.resolve()
        template = (root / filename).resolve()
        if not template.is_relative_to(root) or not template.is_file():
            raise RendererFailure("A trusted report template is unavailable.")
        return template

    @staticmethod
    def _record_error(
        span: trace.Span,
        report: CountyReport | StateReport,
        template_key: str,
        started: float,
        category: str,
    ) -> None:
        duration_ms = (time.perf_counter() - started) * 1_000
        span.set_attribute("atlas.report.duration_ms", duration_ms)
        span.set_attribute("atlas.report.outcome", "failure")
        span.set_attribute("atlas.report.error_category", category)
        span.set_status(Status(StatusCode.ERROR, category))
        _LOGGER.warning(
            "report_render_failed",
            extra={
                "template": template_key,
                "geography_type": report.geography.level,
                "duration_ms": duration_ms,
                "outcome": "failure",
                "error_category": category,
            },
        )


def _item_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value) + sum(_item_count(item) for item in value)
    if isinstance(value, dict):
        return sum(_item_count(item) for item in value.values())
    return 0
