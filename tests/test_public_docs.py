"""Keep the public guide's executable examples aligned with generated OpenAPI."""

import ast
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "docs/public-api-guide.md").read_text(encoding="utf-8")
SCHEMA = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))


def test_curl_examples_match_openapi() -> None:
    urls = re.findall(r"curl -fsS '([^']+)'", GUIDE)
    assert len(urls) >= 7
    for url in urls:
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "api.carawaylabs.com"
        path = parsed.path
        if path not in SCHEMA["paths"]:
            path = re.sub(r"/case_count_floor_2023$", "/{measure_id}", path)
            path = re.sub(r"/human$", "/{source_id}", path)
            path = re.sub(
                r"/human_confirmed_probable_case_floor_v1$", "/{methodology_id}", path
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
