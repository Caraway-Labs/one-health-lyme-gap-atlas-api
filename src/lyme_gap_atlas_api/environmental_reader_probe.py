"""One bounded service-side diagnostic; no HTTP surface or configuration lookup."""

import hashlib
import json
from threading import Thread
from typing import Any

from .config import ApiSettings
from .dependency_telemetry import connect
from .environmental_context import MEASURES
from .telemetry_logging import operational_logger

logger = operational_logger(__name__)
EVENT = "atlas_environmental_reader_probe"
TARGETS = {
    "ONE_HEALTH_LYME_GAP_ATLAS_DEV": ("dev", "OH_LYME_DEV_READ"),
    "ONE_HEALTH_LYME_GAP_ATLAS_PROD": ("prod", "OH_LYME_PROD_READ"),
}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def _failure(error: Exception) -> str:
    # Never copy SDK text, SQL, endpoint, errno outside this closed vocabulary.
    if getattr(error, "errno", None) == 2003:
        return "object_or_access_unavailable"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "dependency_unavailable"


def probe_reader(settings: ApiSettings) -> dict[str, Any]:
    """Three SELECTs maximum, <=5 metadata rows and <=1 observation row.

    The actual primary identity must match the existing approved API principal,
    configured target and environment reader before either presentation read.
    Failed visibility is unavailable, never an assertion of missing objects.
    """
    report: dict[str, Any] = {
        "contract": "atlas-environmental-reader-probe-v1",
        "identity_status": "unverified",
        "metadata_read": "not_attempted",
        "observations_read": "not_attempted",
        "publication_matches": False,
    }
    target = TARGETS.get(settings.presentation_database)
    if target is None or settings.snowflake_presentation_schema != "PRESENTATION":
        return report | {"identity_status": "unsupported_target"}
    environment, expected_role = target
    report["environment"] = environment
    timeout = min(settings.public_query_timeout_seconds, 5)
    try:
        with connect(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT CURRENT_USER(),CURRENT_ROLE(),CURRENT_DATABASE(),CURRENT_WAREHOUSE()",
                timeout=timeout,
            )
            identity = cursor.fetchone()
            if (
                not isinstance(identity, tuple)
                or len(identity) != 4
                or any(not isinstance(value, str) or not value for value in identity)
            ):
                return report | {"identity_status": "invalid_identity"}
            user, role, database, warehouse = identity
            matches = {
                "principal_matches": user == settings.snowflake_user == "OH_LYME_API_SVC",
                "reader_role_matches": role == settings.snowflake_role == expected_role,
                "database_matches": database
                == settings.snowflake_database
                == settings.presentation_database,
                "warehouse_matches": warehouse == settings.snowflake_warehouse,
            }
            report.update(matches)
            report["identity_fingerprint"] = _fingerprint(identity)
            report["warehouse_is_compute"] = warehouse == "COMPUTE_WH"
            if not all(matches.values()):
                return report | {"identity_status": "mismatch"}
            report["identity_status"] = "verified"
            schema = f"{settings.presentation_database}.PRESENTATION"
            metadata = []
            observations = []
            for key, query in (
                (
                    "metadata_read",
                    f"SELECT release_id,measure_id FROM {schema}."
                    "CURRENT_CLIMATE_MEASURE_METADATA_V LIMIT 5",
                ),
                (
                    "observations_read",
                    f"SELECT release_id FROM {schema}."
                    "CURRENT_CLIMATE_COUNTY_DAY_OBSERVATIONS_V LIMIT 1",
                ),
            ):
                try:
                    cursor.execute(query, timeout=timeout)
                    rows = cursor.fetchall()
                    report[key] = "readable_nonempty" if rows else "readable_empty"
                    report[key + "_rows"] = min(len(rows), 5 if key == "metadata_read" else 1)
                    if key == "metadata_read":
                        metadata = rows
                    else:
                        observations = rows
                except Exception as error:
                    report[key] = _failure(error)
            # Count/identity read proof is separate from complete consumer decoding.
            if (
                len(metadata) == 4
                and all(isinstance(row, tuple) and len(row) == 2 for row in metadata)
                and {row[1] for row in metadata} == MEASURES
                and len(observations) == 1
                and isinstance(observations[0], tuple)
                and len(observations[0]) == 1
                and isinstance(observations[0][0], str)
                and observations[0][0]
                and all(row[0] == observations[0][0] for row in metadata)
            ):
                report["publication_matches"] = True
                report["release_fingerprint"] = _fingerprint(observations[0][0])
    except Exception as error:
        report["connection_status"] = _failure(error)
    return report


def log_reader_probe(settings: ApiSettings) -> None:
    logger.info(EVENT, extra={"context": probe_reader(settings)})


def start_reader_probe(settings: ApiSettings) -> None:
    """One daemon per real app factory, independent of activation and readiness.

    Reuse the existing connector/settings; never read/export a credential or app
    specification. Existing connection retry/timeout policy remains in force;
    query count, statement timeout and fetched rows are bounded independently.
    """
    if not settings.snowflake_account or not settings.snowflake_user:
        return
    try:
        Thread(target=log_reader_probe, args=(settings,), daemon=True).start()
    except Exception:
        logger.info(EVENT, extra={"context": {"identity_status": "dispatch_unavailable"}})
