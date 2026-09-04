"""Server-owned, versioned report template definitions."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal


@dataclass(frozen=True)
class TemplateDefinition:
    identifier: str
    relative_path: str
    geography_level: Literal["county", "state"]
    version: str


_TEMPLATES = (
    TemplateDefinition("county-v1", "county/v1/report.typ", "county", "v1"),
    TemplateDefinition("state-v1", "state/v1/report.typ", "state", "v1"),
)

TEMPLATE_REGISTRY: Mapping[str, TemplateDefinition] = MappingProxyType(
    {template.identifier: template for template in _TEMPLATES}
)


def trusted_template_paths() -> Mapping[str, str]:
    """Return only package-relative paths for trusted server-side template keys."""

    return MappingProxyType(
        {template.identifier: template.relative_path for template in TEMPLATE_REGISTRY.values()}
    )
