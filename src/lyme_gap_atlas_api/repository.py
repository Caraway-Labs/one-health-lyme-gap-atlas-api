"""Snowflake read adapter; all browser-visible data crosses this boundary."""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from lyme_gap_atlas_shared.snowflake import connect

from .config import ApiSettings
from .models import AtlasMetadata, CountyRecord, SourceMetadata

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Snapshot:
    metadata: AtlasMetadata
    counties: list[CountyRecord]


class AtlasRepository(Protocol):
    def ready(self) -> bool: ...
    def load_snapshot(self) -> Snapshot: ...


class AtlasDataUnavailableError(RuntimeError):
    """The governed Atlas snapshot cannot be read from its backing service."""


class SnowflakeAtlasRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    @property
    def _presentation_schema(self) -> str:
        return (
            f"{_sql_identifier(self.settings.presentation_database)}."
            f"{_sql_identifier(self.settings.snowflake_presentation_schema)}"
        )

    def ready(self) -> bool:
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
        except Exception as exc:
            logger.warning("atlas_readiness_check_failed", extra={"error_type": type(exc).__name__})
            raise AtlasDataUnavailableError("Atlas data service is unavailable") from exc

    def load_snapshot(self) -> Snapshot:
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    f"""SELECT RELEASE_ID, SCHEMA_VERSION, GENERATED_AT, LOADED_AT, SCOPE,
                          BUNDLE_SHA256, TO_JSON(SCORE_DEFAULTS), METHODOLOGY_VERSION, LIMITATIONS
                   FROM {self._presentation_schema}.CURRENT_RELEASE_V"""
                )
                release = cursor.fetchone()
                if release is None:
                    raise AtlasDataUnavailableError("No current Atlas release is available")
                cursor.execute(
                    f"""SELECT SOURCE_KEY, LABEL, VINTAGE, SOURCE_URL, NOTE
                   FROM {self._presentation_schema}.CURRENT_SOURCE_METADATA_V ORDER BY SOURCE_KEY"""
                )
                sources = [
                    SourceMetadata(
                        key=row[0], label=row[1], vintage=row[2], url=row[3], note=row[4]
                    )
                    for row in cursor.fetchall()
                ]
                cursor.execute(
                    f"""SELECT RELEASE_ID,FIPS,COUNTY,STATE,STATE_NAME,POPULATION,
                          IN_CONTIGUOUS_TICK_SCOPE,HUMAN_STATUS,CASE_COUNT_FLOOR_2023,
                          INCIDENCE_FLOOR_2023,STATE_UNALLOCATED_RECORDS_2023,TICK_STATUS,
                          SCAPULARIS_STATUS,PACIFICUS_STATUS,BURGDORFERI_STATUS,SVI_PERCENTILE,
                          UNINSURED_PERCENTILE,UNINSURED_PERCENT,RUCC_2023,EVIDENCE_COMPLETENESS,
                          TO_JSON(GEOMETRY_JSON)
                   FROM {self._presentation_schema}.CURRENT_COUNTY_ATLAS_V ORDER BY FIPS"""
                )
                counties = [self._county(row) for row in cursor.fetchall()]
            states = sorted({(county.state, county.state_name) for county in counties})
            metadata = AtlasMetadata(
                release_id=release[0],
                schema_version=release[1],
                generated_at=release[2],
                loaded_at=release[3],
                scope=release[4],
                bundle_sha256=release[5],
                score_defaults=json.loads(release[6]),
                methodology_version=release[7],
                limitations=release[8],
                sources=sources,
                states=[{"code": code, "name": name} for code, name in states],
            )
            return Snapshot(metadata=metadata, counties=counties)
        except AtlasDataUnavailableError:
            raise
        except Exception as exc:
            raise AtlasDataUnavailableError("Atlas data service is unavailable") from exc

    @staticmethod
    def _county(row: tuple[Any, ...]) -> CountyRecord:
        return CountyRecord(
            release_id=row[0],
            fips=row[1],
            county=row[2],
            state=row[3],
            state_name=row[4],
            population=row[5],
            in_contiguous_tick_scope=row[6],
            human_status=row[7],
            case_count_floor_2023=row[8],
            incidence_floor_2023=row[9],
            state_unallocated_records_2023=row[10],
            tick_status=row[11],
            scapularis_status=row[12],
            pacificus_status=row[13],
            burgdorferi_status=row[14],
            svi_percentile=row[15],
            uninsured_percentile=row[16],
            uninsured_percent=row[17],
            rucc_2023=row[18],
            evidence_completeness=row[19],
            geometry=json.loads(row[20]),
        )


_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _sql_identifier(value: str) -> str:
    """Quote a configured Snowflake identifier and reject SQL fragments."""
    if not _SQL_IDENTIFIER.fullmatch(value):
        raise ValueError("Snowflake database and schema names must be simple identifiers")
    return f'"{value.upper()}"'
