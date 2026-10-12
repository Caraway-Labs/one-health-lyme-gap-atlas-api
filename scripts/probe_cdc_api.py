"""Genuine HTTP acceptance for API #88; output only public response data."""

import argparse
import json
import time

import httpx

from lyme_gap_atlas_api.cdc_surveillance import IDENTITIES, RELEASE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://api.onehealthatlas.org")
    args = parser.parse_args()
    cases = [
        ("scapularis_status", "13081", "Reported", "OBSERVED", "detected"),
        ("burgdorferi_status", "19135", "Present", "OBSERVED", "detected"),
        ("burgdorferi_status", "12047", "No records", "NO_RECORDS", "unknown"),
    ]
    results = []
    with httpx.Client(timeout=60) as client:
        for measure, county, value, state, normalized in cases:
            params = {
                "measure_id": measure,
                "geography_type": "county",
                "geography_id": county,
                "start_date": "2025-12-31",
                "end_date": "2025-12-31",
            }
            started = time.monotonic()
            response = client.get(args.base_url.rstrip("/") + "/v1/observations", params=params)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            if response.status_code != 200:
                raise SystemExit(f"FAIL {measure}/{county}: HTTP {response.status_code}")
            payload = response.json()
            assert len(payload["data"]) == 1
            item = payload["data"][0]
            assert item["value"] == value and item["value_state"] == state
            assert item["geography"]["geography_id"] == county
            assert item["measure_id"] == measure and item["release_id"] == RELEASE
            assert item["metadata_revision_id"] == IDENTITIES[measure][2]
            assert item["period_start"] == item["period_end"] == "2025-12-31"
            assert item["temporal_grain"] == "CUMULATIVE"
            assert item["surveillance_context"]["evidence"]["normalized_state"] == normalized
            assert item["limitations"] and item["surveillance_context"]["evidence"]["limitations"]
            assert not any(
                k in response.text
                for k in (
                    "authority_reference",
                    "bundle_sha256",
                    "source_version_id",
                    "record_refs",
                    "lineage_id",
                    "cdc_runtime_authority",
                )
            )
            results.append(
                {"request": dict(params), "http_status": 200, "elapsed_ms": elapsed_ms,
                 "response": item}
            )
        params = {**params, "geography_id": "08013"}
        started = time.monotonic()
        unsupported = client.get(args.base_url.rstrip("/") + "/v1/observations", params=params)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if unsupported.status_code != 503:
            print(json.dumps({"accepted": results, "unapproved": {
                "request": params, "http_status": unsupported.status_code,
                "elapsed_ms": elapsed_ms,
                "content_type": unsupported.headers.get("content-type"),
                "upstream_status": unsupported.headers.get("x-do-orig-status"),
            }}, indent=2))
            raise SystemExit(
                f"FAIL burgdorferi_status/08013: HTTP {unsupported.status_code}; "
                f"{elapsed_ms}ms; upstream status "
                f"{unsupported.headers.get('x-do-orig-status', 'unknown')}"
            )
        assert unsupported.headers["content-type"].split(";")[0] == "application/problem+json"
        problem = unsupported.json()
        assert problem["status"] == 503
        assert problem["detail"] == "The governed Atlas data service is temporarily unavailable."
        assert "surveillance_context" not in unsupported.text
        assert not any(
            field in unsupported.text
            for field in ("Snowflake", "LINEAGE_AUDIT", "authority_reference", "record_refs")
        )
    print(json.dumps({"accepted": results, "unapproved_http_status": 503,
                      "unapproved": {"request": params, "elapsed_ms": elapsed_ms,
                                     "response": problem}}, indent=2))


if __name__ == "__main__":
    main()
