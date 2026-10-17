"""Bounded configured-service proof; no new credential or HTTP diagnostic surface."""

from threading import Thread
from typing import Any

from .config import ApiSettings
from .county_context import COLUMNS, context_observation
from .county_context_ids import CONTEXT_CONTRACT, CONTEXT_FIELDS
from .dependency_telemetry import connect
from .environmental_reader_probe import TARGETS, _failure, _fingerprint
from .telemetry_logging import operational_logger

logger = operational_logger(__name__)
EVENT = "atlas_county_context_reader_probe"


def probe_county_context(settings: ApiSettings) -> dict[str, Any]:
    """At most three SELECTs, ten rows decoded, five-second statement cap, no retries."""
    report: dict[str, Any] = {
        "contract": CONTEXT_CONTRACT,
        "identity_status": "unverified",
        "decoded_observations": 0,
        "publication_matches": False,
    }
    target = TARGETS.get(settings.presentation_database)
    if target is None or settings.snowflake_presentation_schema != "PRESENTATION":
        return report | {"identity_status": "unsupported_target"}
    timeout = min(settings.public_query_timeout_seconds, 5)
    try:
        with connect(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT CURRENT_USER(),CURRENT_ROLE(),CURRENT_DATABASE(),CURRENT_WAREHOUSE()",
                timeout=timeout,
            )
            identity = cursor.fetchone()
            if identity != (
                "OH_LYME_API_SVC",
                target[1],
                settings.presentation_database,
                settings.snowflake_warehouse,
            ) or (
                settings.snowflake_user != "OH_LYME_API_SVC"
                or settings.snowflake_role != target[1]
                or settings.snowflake_database != settings.presentation_database
                or not settings.snowflake_warehouse
            ):
                return report | {"identity_status": "mismatch"}
            report["identity_status"] = "verified"
            report["identity_fingerprint"] = _fingerprint(identity)
            schema = f"{settings.presentation_database}.PRESENTATION"
            cursor.execute(
                f"SELECT release_id FROM {schema}.CURRENT_RELEASE_V LIMIT 2", timeout=timeout
            )
            releases = cursor.fetchall()
            if len(releases) != 1 or not releases[0][0]:
                return report | {"read_status": "unavailable_release"}
            release = releases[0][0]
            placeholders = ",".join(["%s"] * len(CONTEXT_FIELDS))
            cursor.execute(
                f"SELECT {COLUMNS} FROM {schema}.CURRENT_COUNTY_OBSERVATIONS_V "
                f"WHERE measure_id IN ({placeholders}) AND context_contract_version=%s "
                "AND county_fips IN ('01001','08001') AND release_version=%s "
                "ORDER BY measure_id,county_fips LIMIT 11",
                (*CONTEXT_FIELDS, CONTEXT_CONTRACT, release),
                timeout=timeout,
            )
            rows = cursor.fetchall()
            if len(rows) != 10:
                return report | {"read_status": "unavailable_membership"}
            observations = [context_observation(row) for row in rows]
            expected = {(mid, fips) for mid in CONTEXT_FIELDS for fips in ("01001", "08001")}
            if (
                len(observations) == 10
                and {(o.measure_id, o.geography.geography_id) for o in observations} == expected
                and all(o.release_id == release for o in observations)
            ):
                report.update(
                    decoded_observations=10,
                    publication_matches=True,
                    release_fingerprint=_fingerprint(release),
                    read_status="verified",
                )
            else:
                report["read_status"] = "unavailable_membership"
    except Exception as error:
        report["read_status"] = _failure(error)
    return report


def _log(settings: ApiSettings) -> None:
    logger.info(EVENT, extra={"context": probe_county_context(settings)})


def start_county_context_probe(settings: ApiSettings) -> None:
    if not settings.snowflake_account or not settings.snowflake_user:
        return
    try:
        Thread(target=_log, args=(settings,), daemon=True).start()
    except Exception:
        logger.info(EVENT, extra={"context": {"identity_status": "dispatch_unavailable"}})
