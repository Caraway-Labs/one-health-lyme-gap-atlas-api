"""Refresh the OpenAPI response examples from anonymous, read-only production GETs.

Run deliberately, not in CI:

    uv run python scripts/capture_openapi_examples.py

Each example is a trimmed capture of a real response (see the README in
``src/lyme_gap_atlas_api/openapi_examples``). The script sends no credentials,
pauses between requests, and fetches each PDF route once. It writes one file
per public operation, named ``<operationId>.json`` from
``tests/fixtures/public-operation-ids.json``. A missing file drops that
operation's example out of the spec.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "lyme_gap_atlas_api" / "openapi_examples"
OPERATION_IDS = json.loads((ROOT / "tests/fixtures/public-operation-ids.json").read_text())
BASE = "https://api.onehealthatlas.org"
PAUSE_SECONDS = 4.0

OBS = "/v1/observations?measure_id=case_count_floor_2023&geography_type=county&year=2023"
OBS_NONE = "/v1/observations?measure_id=case_count_floor_2023&geography_type=county&year=1999"

Json = Any
Trim = Callable[[Any], Any]


def fetch(path: str) -> tuple[bytes, dict[str, str]]:
    request = urllib.request.Request(BASE + path, headers={"User-Agent": "atlas-example-capture"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{path}: expected 200, got {error.code}") from error


def first(count: int, *keys: str) -> Trim:
    """Keep the first ``count`` items of the arrays found at each dotted key."""

    def trim(document: Json) -> Json:
        for key in keys:
            node = document
            *parents, leaf = key.split(".")
            for part in parents:
                node = node[part]
            node[leaf] = node[leaf][:count]
        return document

    return trim


def json_example(name: str, summary: str, path: str, trim: Trim | None = None) -> dict[str, Any]:
    body, _ = fetch(path)
    value = json.loads(body)
    return {
        "request": path,
        "media_type": "application/json",
        "name": name,
        "summary": summary,
        "value": trim(value) if trim else value,
    }


def csv_example() -> dict[str, Any]:
    body, headers = fetch("/v1/atlas/ranking.csv")
    lines = body.decode("utf-8").splitlines()[:3]
    return {
        "request": "/v1/atlas/ranking.csv",
        "media_type": "text/csv",
        "name": "ranking",
        "summary": "Header row and the first two data rows",
        "description": (
            "Response headers: `Content-Type: text/csv; charset=utf-8` and "
            f"`Content-Disposition: {headers['content-disposition']}`. The body lists every "
            "county; this sample shows the header row and two data rows."
        ),
        "value": "\n".join(lines) + "\n",
    }


def geometry_example() -> dict[str, Any]:
    body, headers = fetch("/v1/atlas/geometry")
    collection = json.loads(body)
    feature = next(f for f in collection["features"] if f["id"] == "56045")
    return {
        "request": "/v1/atlas/geometry",
        "media_type": "application/geo+json",
        "name": "counties",
        "summary": "One county feature from the full collection",
        "description": (
            "Response headers: `Content-Type: application/geo+json`, a weak `ETag`, and "
            f"`Cache-Control: {headers['cache-control']}`. The body is a `FeatureCollection` "
            "with one feature per county; this sample keeps one feature."
        ),
        "value": {"type": "FeatureCollection", "features": [feature]},
    }


def pdf_example(path: str, name: str) -> dict[str, Any]:
    body, headers = fetch(path)
    return {
        "request": path,
        "media_type": "application/pdf",
        "name": name,
        "summary": "PDF report (first bytes only)",
        "description": (
            "Response headers: `Content-Type: application/pdf`, "
            f"`Content-Disposition: {headers['content-disposition']}`, a weak `ETag`, and "
            f"`Cache-Control: {headers['cache-control']}`. The body is a binary PDF; "
            "only its first bytes are shown."
        ),
        "value": body[:8].decode("ascii"),
    }


JSON_CAPTURES: dict[str, tuple[str, str, str, Trim | None]] = {
    "/v1/indicators": (
        "indicators",
        "Two indicators from the current release",
        "/v1/indicators",
        first(2, "data"),
    ),
    "/v1/indicators/{indicator_id}": (
        "human_disease_burden",
        "One indicator",
        "/v1/indicators/human_disease_burden",
        None,
    ),
    "/v1/measures": (
        "measures",
        "Two measures from the current release",
        "/v1/measures",
        first(2, "data"),
    ),
    "/v1/measures/{measure_id}": (
        "case_count_floor_2023",
        "One measure",
        "/v1/measures/case_count_floor_2023",
        None,
    ),
    "/v1/sources": (
        "sources",
        "Two sources from the current release",
        "/v1/sources",
        first(2, "data"),
    ),
    "/v1/sources/{source_id}": ("human", "One source", "/v1/sources/human", None),
    "/v1/methodologies/{methodology_id}": (
        "human_confirmed_probable_case_floor_v1",
        "One methodology",
        "/v1/methodologies/human_confirmed_probable_case_floor_v1",
        None,
    ),
    "/v1/geographies/{geography_type}/{geography_id}": (
        "county",
        "County identity while canonical delivery is unpublished",
        "/v1/geographies/county/36061",
        None,
    ),
    "/v1/geographies/{geography_type}/{geography_id}/availability": (
        "unpublished",
        "Confirmed county; canonical geography publication pending",
        "/v1/geographies/county/36061/availability",
        None,
    ),
    "/v1/atlas/metadata": (
        "metadata",
        "Release metadata, trimmed to two sources and two states",
        "/v1/atlas/metadata",
        first(2, "sources", "states"),
    ),
    "/v1/atlas/scores": (
        "scores",
        "Scores for the first two counties of the release",
        "/v1/atlas/scores",
        first(2, "counties"),
    ),
    "/v1/atlas/map-values": (
        "map_values",
        "Map values for the first two counties",
        "/v1/atlas/map-values",
        first(2, "counties"),
    ),
    "/v1/counties/{fips}": (
        "new_york_county",
        "County detail for New York County (`36061`)",
        "/v1/counties/36061",
        None,
    ),
    "/v1/counties/{fips}/tier1-surveillance-priority": (
        "new_york_county",
        "Persisted review priority for `36061`",
        "/v1/counties/36061/tier1-surveillance-priority",
        None,
    ),
    "/v1/counties/{fips}/tier1-surveillance-priority/availability": (
        "available",
        "A county with a persisted review priority",
        "/v1/counties/36061/tier1-surveillance-priority/availability",
        None,
    ),
    "/v1/counties/{fips}/surveillance-situations": (
        "available",
        "Both model variants for `36061`",
        "/v1/counties/36061/surveillance-situations",
        None,
    ),
    "/v1/states/{state}/review": (
        "colorado",
        "County review for Colorado, trimmed to two data gaps",
        "/v1/states/CO/review",
        first(2, "data_gaps"),
    ),
}


def collect() -> dict[str, dict[str, Any]]:
    """Return captures keyed by path, then example name, in request order."""
    captures: dict[str, dict[str, Any]] = {}

    def add(path: str, example: dict[str, Any]) -> None:
        captures.setdefault(path, {})[example["name"]] = example
        time.sleep(PAUSE_SECONDS)

    for path, (name, summary, request, trim) in JSON_CAPTURES.items():
        add(path, json_example(name, summary, request, trim))
    add(
        "/v1/observations",
        json_example(
            "observed", "An observed value: New York County (`36061`)", f"{OBS}&geography_id=36061"
        ),
    )
    add(
        "/v1/observations",
        json_example(
            "missing",
            "A missing value: Boulder County (`08001`) has no published value",
            f"{OBS}&geography_id=08001",
        ),
    )
    add(
        "/v1/observations",
        json_example(
            "empty",
            "An empty result with `meta.availability` set to `UNAVAILABLE`",
            f"{OBS_NONE}&geography_id=36061",
        ),
    )
    add("/v1/atlas/ranking.csv", csv_example())
    add("/v1/atlas/geometry", geometry_example())
    add("/v1/counties/{fips}/report.pdf", pdf_example("/v1/counties/36061/report.pdf", "county"))
    add("/v1/states/{state}/report.pdf", pdf_example("/v1/states/CO/report.pdf", "state"))
    return captures


def write(captures: dict[str, dict[str, Any]]) -> None:
    captured_on = datetime.now(UTC).date().isoformat()
    for path, examples in captures.items():
        operation_id = OPERATION_IDS[path]
        media: dict[str, dict[str, Any]] = {}
        requests: dict[str, str] = {}
        for example in examples.values():
            entry = {"summary": example["summary"], "value": example["value"]}
            if "description" in example:
                entry["description"] = example["description"]
            media.setdefault(example["media_type"], {})[example["name"]] = entry
            requests[example["name"]] = example["request"]
        document = {"_capture": {"captured_on": captured_on, "requests": requests}, **media}
        (OUT / f"{operation_id}.json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote {operation_id}.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    write(collect())
    return 0


if __name__ == "__main__":
    sys.exit(main())
