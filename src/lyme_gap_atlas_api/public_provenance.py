"""Current-release public source and methodology resources (Data #515)."""

from typing import Any, Protocol, cast

from lyme_gap_atlas_shared.snowflake import connect

from .config import ApiSettings
from .public_contract import Methodology, PublicQueryError, Source
from .public_tokens import decode, encode
from .repository import AtlasDataUnavailableError, _sql_identifier


class ProvenanceRepository(Protocol):
    def current_release(self) -> str: ...
    def sources(self, release: str, limit: int, offset: int) -> list[tuple[Any, ...]]: ...
    def source(self, source_id: str, release: str) -> tuple[Any, ...] | None: ...
    def methodology(self, methodology_id: str, release: str) -> tuple[Any, ...] | None: ...


class SnowflakeProvenanceRepository:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.schema = (
            f"{_sql_identifier(settings.presentation_database)}."
            f"{_sql_identifier(settings.snowflake_presentation_schema)}"
        )

    def _read(self, statement: str, params: tuple[Any, ...] = (), *, one: bool = False) -> Any:
        try:
            with connect(self.settings) as connection, connection.cursor() as cursor:
                cursor.execute(
                    statement, params, timeout=self.settings.public_query_timeout_seconds
                )
                return cursor.fetchone() if one else cursor.fetchall()
        except Exception as exc:
            raise AtlasDataUnavailableError("Governed provenance is unavailable") from exc

    def current_release(self) -> str:
        row = self._read(f"SELECT RELEASE_ID FROM {self.schema}.CURRENT_RELEASE_V", one=True)
        if row is None:
            raise AtlasDataUnavailableError("No current governed release")
        return str(row[0])

    def sources(self, release: str, limit: int, offset: int) -> list[tuple[Any, ...]]:
        return cast(
            list[tuple[Any, ...]],
            self._read(
                "SELECT SOURCE_KEY, LABEL, VINTAGE, SOURCE_URL, NOTE, SOURCE_ID, DATASET_ID, "
                "PUBLISHER, UPSTREAM_UPDATED_AT, SOURCE_RETRIEVED_AT, "
                "SEMANTIC_CONTRACT_VERSION, RELEASE_VERSION "
                f"FROM {self.schema}.CURRENT_SOURCE_METADATA_V "
                "WHERE RELEASE_VERSION = %s ORDER BY SOURCE_KEY LIMIT %s OFFSET %s",
                (release, limit, offset),
            ),
        )

    def source(self, source_id: str, release: str) -> tuple[Any, ...] | None:
        return cast(
            tuple[Any, ...] | None,
            self._read(
                "SELECT SOURCE_KEY, LABEL, VINTAGE, SOURCE_URL, NOTE, SOURCE_ID, DATASET_ID, "
                "PUBLISHER, UPSTREAM_UPDATED_AT, SOURCE_RETRIEVED_AT, "
                "SEMANTIC_CONTRACT_VERSION, RELEASE_VERSION "
                f"FROM {self.schema}.CURRENT_SOURCE_METADATA_V "
                "WHERE SOURCE_KEY = %s AND RELEASE_VERSION = %s LIMIT 1",
                (source_id, release),
                one=True,
            ),
        )

    def methodology(self, methodology_id: str, release: str) -> tuple[Any, ...] | None:
        return cast(
            tuple[Any, ...] | None,
            self._read(
                "SELECT METHODOLOGY_ID, MEASURE_ID, METHODOLOGY, METHODOLOGY_VERSION, "
                "LIMITATION, SEMANTIC_CONTRACT_VERSION, RELEASE_VERSION "
                f"FROM {self.schema}.CURRENT_METHODOLOGY_METADATA_V "
                "WHERE METHODOLOGY_ID = %s AND RELEASE_VERSION = %s LIMIT 1",
                (methodology_id, release),
                one=True,
            ),
        )


class ProvenanceService:
    def __init__(self, repository: ProvenanceRepository) -> None:
        self.repository = repository

    @staticmethod
    def _source(row: tuple[Any, ...]) -> Source:
        try:
            return Source(
                source_id=row[0],
                label=row[1],
                source_vintage=row[2],
                source_url=row[3],
                limitations=[row[4]] if row[4] else [],
                lineage_source_id=row[5],
                dataset_id=row[6],
                publisher=row[7],
                upstream_updated_at=row[8],
                source_retrieved_at=row[9],
                semantic_version=row[10],
                release_version=row[11],
            )
        except (ValueError, TypeError) as exc:
            raise AtlasDataUnavailableError("Unsupported governed source metadata") from exc

    @staticmethod
    def _methodology(row: tuple[Any, ...]) -> Methodology:
        try:
            return Methodology(
                methodology_id=row[0],
                measure_id=row[1],
                description=row[2],
                version=row[3],
                limitations=[row[4]] if row[4] else [],
                semantic_version=row[5],
                release_version=row[6],
            )
        except (ValueError, TypeError) as exc:
            raise AtlasDataUnavailableError("Unsupported governed methodology metadata") from exc

    def sources(self, size: int, token: str | None) -> tuple[list[Source], str | None]:
        release = self.repository.current_release()
        offset = 0
        if token:
            if len(token) > 2048:
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.")
            try:
                payload = decode(token)
                offset = payload["offset"]
                if (
                    payload["release"] != release
                    or type(offset) is not int
                    or offset < 1
                    or offset > 10_000
                ):
                    raise ValueError
            except (ValueError, KeyError, TypeError) as exc:
                raise PublicQueryError("INVALID_REQUEST", "Invalid page_token.") from exc
        rows = self.repository.sources(release, size + 1, offset)
        page = [self._source(row) for row in rows[:size]]
        next_token = None
        if len(rows) > size:
            next_token = encode({"release": release, "offset": offset + size})
        return page, next_token

    def source(self, source_id: str) -> Source:
        release = self.repository.current_release()
        row = self.repository.source(source_id, release)
        if row is None:
            raise PublicQueryError("RESOURCE_NOT_FOUND", "Source not found.")
        return self._source(row)

    def methodology(self, methodology_id: str) -> Methodology:
        release = self.repository.current_release()
        row = self.repository.methodology(methodology_id, release)
        if row is None:
            raise PublicQueryError("RESOURCE_NOT_FOUND", "Methodology not found.")
        return self._methodology(row)
