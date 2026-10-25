"""Bounded anonymous production canaries; never modifies model publication."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

from lyme_gap_atlas_api.surveillance_situations import CountySurveillanceSituations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://api.onehealthatlas.org")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fixture = Path(__file__).resolve().parents[1] / (
        "tests/fixtures/surveillance-situations-prod-2026-10-19.json"
    )
    stored = json.loads(fixture.read_text())
    receipt: dict[str, object] = {
        "verified_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "identity_evidence": "Anonymous HTTP; server uses configured API identity",
    }
    responses = []
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        for fips in sorted({a["COUNTY_FIPS"] for a, _ in stored}):
            started = perf_counter()
            response = client.get(f"/v1/counties/{fips}/surveillance-situations")
            response.raise_for_status()
            result = CountySurveillanceSituations.model_validate(response.json())
            assert result.county_fips == fips
            assert response.headers["cache-control"] == "no-store"
            for variant in (result.four_state, result.tick_pathogen):
                assert variant.availability == "AVAILABLE" and variant.data is not None
                data = variant.data
                original = next(
                    d
                    for a, d in stored
                    if a["COUNTY_FIPS"] == fips and a["MODEL_ID"] == data.model_id
                )
                for key in (
                    "class_id",
                    "class_name",
                    "class_description",
                    "model_version",
                    "batch_id",
                    "batch_version",
                    "fit_git_sha",
                    "input_release_id",
                    "input_bundle_sha256",
                    "county_sha256",
                    "dictionary_sha256",
                    "source_provenance",
                    "model_provenance",
                    "feature_contract",
                ):
                    assert getattr(data, key) == original[key.upper()], key
                assert [p.model_dump() for p in data.probabilities] == original["PROBABILITIES"]
            responses.append(
                {
                    "fips": fips,
                    "status": response.status_code,
                    "elapsed_seconds": round(perf_counter() - started, 3),
                    "request_id": response.headers.get("x-request-id"),
                    "response": response.json(),
                }
            )
        errors = []
        for fips, expected in (("99999", 404), ("1234", 422)):
            response = client.get(f"/v1/counties/{fips}/surveillance-situations")
            assert response.status_code == expected, response.text
            assert response.headers["content-type"].startswith("application/problem+json")
            errors.append({"fips": fips, "status": expected, "response": response.json()})
        schema = client.get("/openapi.json")
        schema.raise_for_status()
        operation = schema.json()["paths"]["/v1/counties/{fips}/surveillance-situations"]["get"]
        assert operation["operationId"] == "getCountySurveillanceSituations"
    receipt.update(responses=responses, errors=errors, result="PASS")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: six production counties, both variants, all classes, 404/422; {args.output}")


if __name__ == "__main__":
    main()
