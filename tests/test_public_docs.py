"""Keep the public guide's executable examples aligned with generated OpenAPI."""

import ast
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lyme_gap_atlas_api.cdc_surveillance import IDENTITIES, PERIOD
from lyme_gap_atlas_api.public_contract import ValueState

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs/public-api-guide.md").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))


def test_curl_examples_match_openapi() -> None:
    urls = re.findall(r"curl -fsS '([^']+)'", GUIDE)
    assert len(urls) >= 7
    for url in urls:
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "api.onehealthatlas.org"
        path = parsed.path
        if path not in SCHEMA["paths"]:
            path = re.sub(r"/case_count_floor_2023$", "/{measure_id}", path)
            path = re.sub(r"/human$", "/{source_id}", path)
            path = re.sub(r"/human_confirmed_probable_case_floor_v1$", "/{methodology_id}", path)
            path = re.sub(
                r"/counties/\d{5}/tier1-surveillance-priority/availability$",
                "/counties/{fips}/tier1-surveillance-priority/availability",
                path,
            )
        operation = SCHEMA["paths"][path]["get"]
        allowed = {item["name"] for item in operation.get("parameters", [])}
        assert set(parse_qs(parsed.query)) <= allowed, url


def test_python_example_is_valid_and_uses_documented_query() -> None:
    code = re.search(r"```python\n(.*?)\n```", GUIDE, re.DOTALL)
    assert code is not None
    ast.parse(code.group(1))
    assert '"page_token"' in code.group(1)
    assert '"case_count_floor_2023"' in code.group(1)


def test_guide_documents_all_value_states_and_cumulative_cdc_selector() -> None:
    states = set(re.findall(r"^\| `([A-Z_]+)` \|", GUIDE, re.MULTILINE))
    assert states == {state.value for state in ValueState}
    assert "normalized state is unknown, never biological absence" in GUIDE
    urls = re.findall(r"curl -fsS '([^']+)'", GUIDE)
    cdc = [
        parse_qs(urlsplit(url).query)
        for url in urls
        if parse_qs(urlsplit(url).query).get("measure_id", [None])[0] in IDENTITIES
    ]
    assert cdc
    for query in cdc:
        assert "year" not in query
        assert query["start_date"] == query["end_date"] == [PERIOD.isoformat()]


def test_landing_description_is_a_quickstart_within_budget() -> None:
    from lyme_gap_atlas_api.public_docs import API_DESCRIPTION

    assert "curl" in API_DESCRIPTION
    assert "https://api.onehealthatlas.org" in API_DESCRIPTION
    assert "no API key" in API_DESCRIPTION
    assert "case_count_floor_2023" in API_DESCRIPTION
    assert "36061" in API_DESCRIPTION
    assert "Contract consumers" not in API_DESCRIPTION
    assert "Fumadocs" not in API_DESCRIPTION
    assert len(API_DESCRIPTION) < 3200
    for heading in (
        "Quickstart",
        "Responses",
        "Pagination",
        "Errors",
        "Response headers and caching",
        "Rate limits and retries",
        "Interpreting results",
        "Stability",
    ):
        assert f"## {heading}" in API_DESCRIPTION
