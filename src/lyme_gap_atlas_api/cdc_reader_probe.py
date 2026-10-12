"""Read-only in-container service identity/consumer acceptance; safe JSON only."""

import json
from typing import Any

from .cdc_surveillance import _read
from .config import ApiSettings
from .dependency_telemetry import connect


def _probe() -> dict[str, Any]:
    settings = ApiSettings()
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("USE SECONDARY ROLES NONE")
        cursor.execute(
            "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_DATABASE(), CURRENT_WAREHOUSE()",
            timeout=15,
        )
        row = cursor.fetchone()
    expected = (
        "OH_LYME_API_SVC",
        "OH_LYME_PROD_READ",
        "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        "COMPUTE_WH",
    )
    if row is None or tuple(row) != expected:
        raise SystemExit("CDC service identity verification failed")
    results = []
    # Regression samples only, never an eligibility list.
    samples = {
        "scapularis_status": ("13081",),
        "burgdorferi_status": ("12047", "19135"),
    }
    for measure, counties in samples.items():
        for county in sorted(counties):
            envelope = _read(measure, county, settings=settings)
            results.append(
                {
                    "measure": measure,
                    "county": county,
                    "normalized_state": envelope["surveillance_evidence"]["normalized_state"],
                }
            )
    return {
        "identity": dict(zip(("user", "role", "database", "warehouse"), expected, strict=True)),
        "consumer_reads": results,
    }


def main() -> None:
    try:
        print(json.dumps(_probe()))
    except Exception:
        raise SystemExit("CDC service acceptance probe unavailable") from None


if __name__ == "__main__":
    main()
