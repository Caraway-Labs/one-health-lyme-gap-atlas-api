import pytest

from lyme_gap_atlas_api.reports.renderer import UnknownTemplateError
from lyme_gap_atlas_api.reports.renderers.typst import TypstRenderer
from lyme_gap_atlas_api.reports.template_registry import TEMPLATE_REGISTRY, trusted_template_paths


def test_registry_exposes_only_versioned_server_template_paths() -> None:
    assert set(TEMPLATE_REGISTRY) == {"county-v1", "state-v1"}
    assert trusted_template_paths() == {
        "county-v1": "county/v1/report.typ",
        "state-v1": "state/v1/report.typ",
    }
    assert all(
        ".." not in path and not path.startswith("/") for path in trusted_template_paths().values()
    )


def test_default_renderer_rejects_unknown_or_path_like_template_keys() -> None:
    renderer = TypstRenderer.__new__(TypstRenderer)
    renderer._templates = dict(trusted_template_paths())
    with pytest.raises(UnknownTemplateError):
        renderer._trusted_template("../county/v1/report.typ")
    with pytest.raises(UnknownTemplateError):
        renderer._trusted_template("unknown-v1")
