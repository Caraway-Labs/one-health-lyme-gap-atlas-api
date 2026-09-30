"""Bounded, anonymous public API smoke and latency probe; no credentials or payload logging."""

import argparse
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request


def get(
    base: str, path: str, params: list[tuple[str, str]] | None = None
) -> tuple[int, dict, float]:
    query = urllib.parse.urlencode(params or [])
    url = base.rstrip("/") + path + ("?" + query if query else "")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, json.load(response), (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as error:
        return error.code, json.load(error), (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://api.carawaylabs.com")
    parser.add_argument("--samples", type=int, choices=(1, 3), default=3)
    args = parser.parse_args()
    base = args.base_url
    observations = [
        ("measure_id", "case_count_floor_2023"),
        ("geography_type", "county"),
        ("year", "2023"),
    ]
    scenarios = {
        "indicators": ("/v1/indicators", []),
        "measures": ("/v1/measures", []),
        "one_county": ("/v1/observations", observations + [("geography_id", "01003")]),
        "three_counties": (
            "/v1/observations",
            observations + [("geography_id", fips) for fips in ("01001", "01003", "01005")],
        ),
        "large_allowed": (
            "/v1/observations",
            observations
            + [("page_size", "500")]
            + [("geography_id", f"{i:05d}") for i in range(1001, 1401)],
        ),
    }
    results = {}
    for name, (path, params) in scenarios.items():
        samples = [get(base, path, params) for _ in range(args.samples)]
        if any(status != 200 for status, _, _ in samples):
            raise SystemExit(f"{name}: expected 200, got {[status for status, _, _ in samples]}")
        durations = [duration for _, _, duration in samples]
        if max(durations) >= 20_000:
            raise SystemExit(f"{name}: exceeded the provisional 20-second release ceiling")
        results[name] = {
            "requests": args.samples,
            "median_ms": round(statistics.median(durations)),
            "max_ms": round(max(durations)),
            "result_count": len(samples[-1][1]["data"]),
        }
    status, observation, _ = get(
        base,
        "/v1/observations",
        observations + [("geography_id", "01001")],
    )
    assert status == 200 and observation["data"]
    item = observation["data"][0]
    assert item["value_state"] == "MISSING" and item["value"] is None
    for path, identifier in (
        ("/v1/sources/", item["source_id"]),
        ("/v1/methodologies/", item["methodology_id"]),
    ):
        status, resource, _ = get(base, path + identifier)
        assert status == 200
        assert resource["data"]["semantic_version"] == item["semantic_version"]
        assert resource["data"]["release_version"] == item["release_id"]
    status, problem, _ = get(
        base,
        "/v1/observations",
        [
            ("measure_id", "unknown"),
            ("geography_type", "county"),
            ("geography_id", "01003"),
            ("year", "2023"),
        ],
    )
    assert status == 404 and problem["code"] == "RESOURCE_NOT_FOUND"
    print(json.dumps({"base_url": base, "scenarios": results, "workflow": "passed"}, indent=2))


if __name__ == "__main__":
    main()
