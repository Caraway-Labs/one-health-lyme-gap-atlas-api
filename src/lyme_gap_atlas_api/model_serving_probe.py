"""Bounded model-view access proof using the API's existing configured identity."""

from threading import Thread
from typing import Any

from .config import ApiSettings
from .dependency_telemetry import connect
from .environmental_reader_probe import TARGETS, _failure, _fingerprint
from .telemetry_logging import operational_logger

logger = operational_logger(__name__)
EVENT = "atlas_model_serving_reader_probe"
MODELS = {"surveillance-situation-four-state", "surveillance-situation-tick-pathogen"}
INPUT_RELEASE = "governed-2026-10-15-january-climate-prod"
INPUT_BUNDLE = "53e50aa37c701635e96c48cd4931c4c4a9e7e695c146263fb9bb335e541ae74d"
BASE_TABLES = (
    "TIER1_COUNTY_REVIEW_OUTPUTS",
    "COUNTY_SURVEILLANCE_SITUATIONS",
    "SURVEILLANCE_SITUATION_BATCHES",
    "SURVEILLANCE_SITUATION_AUTHORIZATIONS",
)


def probe_models(settings: ApiSettings) -> dict[str, Any]:
    """Seven SELECTs maximum, <=5 seconds each, no writes or credential export.

    This proves service access to the two published models, not scientific
    admission of Tier 1, complete release acceptance or public API delivery.
    """
    report: dict[str, Any] = {
        "contract": "atlas-model-serving-reader-probe-v1",
        "identity_status": "unverified",
        "publication_matches": False,
        "base_tables_inaccessible": 0,
    }
    target = TARGETS.get(settings.presentation_database)
    if (
        target is None
        or target[0] != "prod"
        or settings.snowflake_presentation_schema != "PRESENTATION"
    ):
        return report | {"identity_status": "unsupported_target"}
    timeout = min(settings.public_query_timeout_seconds, 5)
    try:
        with connect(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT CURRENT_USER(),CURRENT_ROLE(),CURRENT_DATABASE(),CURRENT_WAREHOUSE(),"
                "IS_ROLE_IN_SESSION('OH_LYME_PROD_OWNER'),"
                "IS_ROLE_IN_SESSION('OH_LYME_PROD_MIGRATION_DEPLOYER'),"
                "IS_ROLE_IN_SESSION('OH_LYME_PROD_ML_PUBLISHER')",
                timeout=timeout,
            )
            identity = cursor.fetchone()
            expected = (
                "OH_LYME_API_SVC",
                target[1],
                settings.presentation_database,
                settings.snowflake_warehouse,
            )
            if (
                identity is None
                or tuple(identity[:4]) != expected
                or tuple(identity[4:]) != (False, False, False)
                or settings.snowflake_user != expected[0]
                or settings.snowflake_role != expected[1]
                or settings.snowflake_database != expected[2]
                or not expected[3]
            ):
                return report | {"identity_status": "mismatch"}
            report["identity_status"] = "verified"
            report["identity_fingerprint"] = _fingerprint(identity[:4])
            schema = f"{settings.presentation_database}.PRESENTATION"
            cursor.execute(
                f"SELECT release_id,bundle_sha256 FROM {schema}.CURRENT_RELEASE_V LIMIT 2",
                timeout=timeout,
            )
            releases = cursor.fetchall()
            if len(releases) != 1 or not all(releases[0]):
                return report | {"read_status": "unavailable_release"}
            release, bundle = releases[0]
            cursor.execute(
                "SELECT model_id,COUNT(*),COUNT(DISTINCT county_fips),"
                "COUNT_IF(release_id IS NULL OR release_id<>%s OR bundle_sha256 IS NULL "
                "OR bundle_sha256<>%s OR input_release_id IS NULL OR input_release_id<>%s "
                "OR input_bundle_sha256 IS NULL OR input_bundle_sha256<>%s "
                "OR model_version IS NULL OR model_version<>'v1' OR batch_id IS NULL "
                "OR batch_id<>'surveillance-situation-batch' OR batch_version IS NULL "
                "OR batch_version<>'v1') "
                f"FROM {schema}.CURRENT_COUNTY_SURVEILLANCE_SITUATIONS_V "
                "GROUP BY model_id ORDER BY model_id LIMIT 3",
                (release, bundle, INPUT_RELEASE, INPUT_BUNDLE),
                timeout=timeout,
            )
            models = cursor.fetchall()
            if (
                len(models) != 2
                or {r[0] for r in models} != MODELS
                or any(tuple(r[1:]) != (3144, 3144, 0) for r in models)
            ):
                return report | {"read_status": "unavailable_membership"}
            report["model_count"] = 2
            report["county_rows"] = 6288
            report["release_fingerprint"] = _fingerprint((release, bundle))
            # Denial-safe reads only. Never invoke a publisher or attempt DML.
            for table in BASE_TABLES:
                try:
                    cursor.execute(
                        f"SELECT * FROM {settings.presentation_database}.FEATURE_STORE."
                        f"{table} LIMIT 0",
                        timeout=timeout,
                    )
                except Exception as error:
                    # Snowflake 2003 uses 02000 for an inaccessible schema,
                    # and 42S02 for an inaccessible table. Neither proves
                    # existence or a denied grant; other errors remain failures.
                    if getattr(error, "errno", None) != 2003 or getattr(
                        error, "sqlstate", None
                    ) not in {"42S02", "02000"}:
                        return report | {"read_status": "denial_unverified"}
                    report["base_tables_inaccessible"] += 1
                else:
                    return report | {"read_status": "base_table_access_unexpected"}
            report.update(publication_matches=True, read_status="verified")
    except Exception as error:
        report["read_status"] = _failure(error)
    return report


def _log(settings: ApiSettings) -> None:
    logger.info(EVENT, extra={"context": probe_models(settings)})


def start_model_serving_probe(settings: ApiSettings) -> None:
    if not settings.snowflake_account or not settings.snowflake_user:
        return
    try:
        Thread(target=_log, args=(settings,), daemon=True).start()
    except Exception:
        logger.info(EVENT, extra={"context": {"identity_status": "dispatch_unavailable"}})
