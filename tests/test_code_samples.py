"""Generated `x-codeSamples` in the public OpenAPI document."""

import ast
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.code_samples import operation_samples
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.public_docs import EXAMPLES_DIR

ORIGIN = "https://api.onehealthatlas.org"
APP = create_app(settings=ApiSettings())
SCHEMA = APP.openapi()
OPERATIONS = [(path, item["get"]) for path, item in SCHEMA["paths"].items()]
PAGED = {"/v1/indicators", "/v1/measures", "/v1/observations", "/v1/sources"}
PDF = {"/v1/counties/{fips}/report.pdf", "/v1/states/{state}/report.pdf"}


def _samples(operation: dict[str, Any]) -> dict[str, str]:
    return {sample["label"]: sample["source"] for sample in operation["x-codeSamples"]}


def _curl_url(source: str) -> str:
    match = re.search(r"'(https://[^']+)'", source)
    assert match
    return match.group(1)


def test_every_public_operation_has_the_four_language_samples() -> None:
    for path, operation in OPERATIONS:
        samples = _samples(operation)
        if path in PDF:
            assert list(samples) == ["curl"]
            assert "-o report.pdf" in samples["curl"]
        else:
            assert list(samples) == ["curl", "Python", "TypeScript", "R"], path
        assert {s["lang"] for s in operation["x-codeSamples"]} <= {
            "Shell",
            "Python",
            "TypeScript",
            "R",
        }


def test_samples_are_absent_from_the_first_party_document() -> None:
    first_party = json.dumps(APP.first_party_openapi())
    assert "x-codeSamples" not in first_party


def test_samples_are_deterministic_and_use_the_server_url() -> None:
    assert SCHEMA["servers"][0]["url"] == ORIGIN
    for path, operation in OPERATIONS:
        assert operation_samples(path, operation, ORIGIN) == operation["x-codeSamples"]
        assert all(ORIGIN in sample["source"] for sample in operation["x-codeSamples"])


@pytest.mark.parametrize(("path", "operation"), OPERATIONS)
def test_python_samples_parse(path: str, operation: dict[str, Any]) -> None:
    if path not in PDF:
        ast.parse(_samples(operation)["Python"])


@pytest.mark.parametrize(("path", "operation"), OPERATIONS)
def test_typescript_and_r_samples_are_well_formed(path: str, operation: dict[str, Any]) -> None:
    if path in PDF:
        return
    samples = _samples(operation)
    typescript, r = samples["TypeScript"], samples["R"]
    assert "await fetch(" in typescript
    for text in (typescript, r):
        assert text.count("(") == text.count(")")
        assert text.count("{") == text.count("}")
        assert text.count("[") == text.count("]")
        assert text.count('"') % 2 == 0
    assert r.startswith("library(httr2)")
    assert "req_perform()" in r


@pytest.mark.parametrize(("path", "operation"), OPERATIONS)
def test_curl_url_matches_a_route_and_declared_parameters(
    path: str, operation: dict[str, Any]
) -> None:
    url = _curl_url(_samples(operation)["curl"])
    parts = urlsplit(url)
    request = TestClient(APP).build_request("GET", url)
    assert str(request.url) == url
    template = re.sub(r"\\{[^}]+\\}", "[^/]+", re.escape(path))
    assert re.fullmatch(template, parts.path), (template, parts.path)
    declared = {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}
    assert {name for name, _ in parse_qsl(parts.query)} <= declared | {"page_token"}


def test_fips_codes_keep_leading_zeros_and_arrays_repeat() -> None:
    county = _samples(SCHEMA["paths"]["/v1/counties/{fips}"]["get"])
    assert "/v1/counties/08001'" in county["curl"]
    assert '"https://api.onehealthatlas.org/v1/counties/08001"' in county["Python"]
    observations = _samples(SCHEMA["paths"]["/v1/observations"]["get"])
    assert "geography_id=08001" in observations["curl"]
    assert '"geography_id": "08001"' in observations["Python"]
    assert '["geography_id", "08001"]' in observations["TypeScript"]
    assert 'geography_id = "08001"' in observations["R"]
    operation = {
        "parameters": [
            {
                "name": "geography_id",
                "in": "query",
                "required": True,
                "schema": {"examples": [["08001", "36061"]]},
            },
            {
                "name": "q",
                "in": "query",
                "required": True,
                "schema": {"examples": ["a b&c/é'"]},
            },
        ],
        "responses": {"200": {"content": {"application/json": {}}}},
    }
    samples = {s["label"]: s["source"] for s in operation_samples("/v1/x", operation, ORIGIN)}
    assert "geography_id=08001&geography_id=36061&q=a%20b%26c%2F%C3%A9%27" in samples["curl"]
    assert '"geography_id": ["08001", "36061"]' in samples["Python"]
    assert samples["R"].count('.multi = "explode"') == 1
    assert 'geography_id = c("08001", "36061")' in samples["R"]
    ast.parse(samples["Python"])


def test_paged_operations_show_the_page_token_loop_in_each_language() -> None:
    for path in PAGED:
        samples = _samples(SCHEMA["paths"][path]["get"])
        for label, source in samples.items():
            assert "page_token" in source, (path, label)
        assert "while True" in samples["Python"]
        assert "while (pageToken !== null)" in samples["TypeScript"]
        assert "repeat {" in samples["R"]
        assert "while [ -n" in samples["curl"]
    assert "page_token" not in _samples(SCHEMA["paths"]["/v1/counties/{fips}"]["get"])["Python"]


def _capture_paths() -> Iterator[str]:
    for file in EXAMPLES_DIR.glob("*.json"):
        capture = json.loads(file.read_text(encoding="utf-8"))["_capture"]["requests"]
        yield from (urlsplit(request).path for request in capture.values())


def test_samples_cover_the_same_endpoints_as_the_captured_examples() -> None:
    sample_shapes = {re.sub(r"\{[^}]+\}", "*", path) for path, _ in OPERATIONS}
    for captured in set(_capture_paths()):
        shape = re.sub(r"/(county|\d{5}|CO|[a-z_]+)(?=/|$)", "/*", captured)
        assert any(
            re.fullmatch(re.escape(s).replace(r"\*", "[^/]+"), captured) for s in sample_shapes
        ), (captured, shape)


class _Recorder:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        token = None if len(self.urls) >= 3 else f"tok{len(self.urls)}"
        return httpx.Response(200, json={"data": [], "meta": {"next_page_token": token}})


def _run_python(source: str, monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    recorder = _Recorder()
    transport = httpx.MockTransport(recorder.handler)
    real_client = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: client().get(url, **kw))
    exec(compile(source, "<sample>", "exec"), {})  # noqa: S102
    return recorder


@pytest.mark.parametrize(("path", "operation"), [o for o in OPERATIONS if o[0] not in PDF | PAGED])
def test_python_sample_requests_the_same_url_as_curl(
    path: str, operation: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    samples = _samples(operation)
    recorder = _run_python(samples["Python"], monkeypatch)
    expected = _curl_url(samples["curl"])
    assert len(recorder.urls) == 1
    assert recorder.urls[0].split("?")[0] == expected.split("?")[0]
    assert sorted(urlsplit(recorder.urls[0]).query.split("&")) == sorted(
        urlsplit(expected).query.split("&")
    )


def test_observations_python_loop_follows_page_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = _samples(SCHEMA["paths"]["/v1/observations"]["get"])
    recorder = _run_python(samples["Python"], monkeypatch)
    assert len(recorder.urls) == 3
    assert "page_token" not in recorder.urls[0]
    assert recorder.urls[1].endswith("page_token=tok1")
    assert recorder.urls[2].endswith("page_token=tok2")
    assert recorder.urls[0].count("geography_id=08001") == 1
