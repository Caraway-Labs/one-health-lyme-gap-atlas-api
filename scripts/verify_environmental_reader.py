"""Read-only proof with the configured API identity; never creates access grants.

Run inside the existing authorized API execution environment after publication.
Successful human PAT or fixture results do not establish actual service access.
"""

import json

from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.dependency_telemetry import connect
from lyme_gap_atlas_api.environmental_context import (
    FIELDS,
    MEASURES,
    EnvironmentalRepository,
    environmental_observation,
)


def main() -> None:
    settings = ApiSettings()
    if not settings.environmental_context_enabled:
        raise SystemExit("ENVIRONMENTAL_CONTEXT_DISABLED")
    repository = EnvironmentalRepository(settings)
    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT CURRENT_USER(),CURRENT_ROLE(),CURRENT_DATABASE(),CURRENT_WAREHOUSE()",
            timeout=settings.public_query_timeout_seconds,
        )
        user, role, database, warehouse = cursor.fetchone()
        if settings.presentation_database not in {
            "ONE_HEALTH_LYME_GAP_ATLAS_DEV",
            "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
        }:
            raise SystemExit("ENVIRONMENTAL_DATABASE")
        cursor.execute(
            f"SELECT release_id,measure_id FROM {repository.schema}."
            "CURRENT_CLIMATE_MEASURE_METADATA_V ORDER BY measure_id",
            timeout=settings.public_query_timeout_seconds,
        )
        metadata = cursor.fetchall()
        if len(metadata) != 4 or {r[1] for r in metadata} != MEASURES:
            raise SystemExit("ENVIRONMENTAL_METADATA_NOT_PUBLISHED")
        release = metadata[0][0]
        if any(r[0] != release for r in metadata):
            raise SystemExit("ENVIRONMENTAL_METADATA_RELEASE")
        cursor.execute(
            f"SELECT {','.join(FIELDS)} FROM {repository.schema}."
            "CURRENT_CLIMATE_COUNTY_DAY_OBSERVATIONS_V "
            "WHERE release_id=%s ORDER BY county_fips,period_start,measure_id LIMIT 4",
            (release,),
            timeout=settings.public_query_timeout_seconds,
        )
        observations = [environmental_observation(row) for row in cursor.fetchall()]
        if len(observations) != 4:
            raise SystemExit("ENVIRONMENTAL_OBSERVATIONS_NOT_PUBLISHED")
    print(
        json.dumps(
            {
                "proof": "CONFIGURED_API_READER_READ_ONLY",
                "user": user,
                "role": role,
                "database": database,
                "warehouse": warehouse,
                "release_id": release,
                "metadata_rows": 4,
                "decoded_observations": len(observations),
                "grants_changed": False,
                "publication_writes": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
