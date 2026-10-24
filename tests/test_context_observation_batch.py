"""County-context Snowflake batch read (#288): one execute on success."""

from datetime import date, datetime
from typing import Any

import pytest

from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.county_context import (
    FIELDS,
    context_query_with_release,
    parse_context_batch_rows,
)
from lyme_gap_atlas_api.county_context_ids import CONTEXT_CONTRACT, CONTEXT_FIELDS
from lyme_gap_atlas_api.public_contract import ObservationQuery, PublicQueryError
from lyme_gap_atlas_api.public_observations import (
    ObservationService,
    SnowflakeObservationRepository,
)
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError
from lyme_gap_atlas_api.snowflake_read_session import get_read_session_pool


@pytest.fixture(autouse=True)
def _isolated_read_pool() -> None:
    get_read_session_pool().close_all()
    yield
    get_read_session_pool().close_all()


def _query(**changes: Any) -> ObservationQuery:
    base = {
        "measure_id": "population_2022",
        "geography_type": "county",
        "geography_id": ["01003"],
        "vintage_year": 2022,
        "page_size": 100,
    }
    base.update(changes)
    return ObservationQuery(**base)


def test_context_query_with_release_sql_shape() -> None:
    view = "DB.SCHEMA.CURRENT_COUNTY_OBSERVATIONS_V"
    schema = "DB.SCHEMA"
    sql, params = context_query_with_release(view, schema, _query(), 0)
    assert "CURRENT_RELEASE_V LIMIT 2" in sql
    assert "LEFT JOIN LATERAL" not in sql
    assert "LEFT JOIN" in sql and "v.release_version=rel.RELEASE_ID" in sql
    assert "TO_JSON(v.value) AS value_json" in sql
    assert "COUNT(*) FROM release_rows)=1" in sql
    assert "ORDER BY v.measure_id" in sql and "LIMIT %s OFFSET %s" in sql
    assert params[0 : len(CONTEXT_FIELDS)] == tuple(CONTEXT_FIELDS)
    assert params[len(CONTEXT_FIELDS)] == "population_2022"
    assert params[len(CONTEXT_FIELDS) + 1] == "01003"
    assert params[-2:] == (101, 0)
    assert CONTEXT_CONTRACT in params and 2022 in params


def test_parse_batch_rows_strips_release_prefix() -> None:
    obs = tuple(f"col-{index}" for index in range(len(FIELDS)))
    release, rows = parse_context_batch_rows([("rel-1", *obs)])
    assert release == "rel-1"
    assert rows == [obs]


def test_parse_batch_empty_observation_columns() -> None:
    release, rows = parse_context_batch_rows([("rel-1", *([None] * len(FIELDS)))])
    assert release == "rel-1"
    assert rows == []


def test_parse_batch_no_release_rows_raises() -> None:
    with pytest.raises(AtlasDataUnavailableError, match="No current governed release"):
        parse_context_batch_rows([])


class _Cursor:
    def __init__(
        self,
        *,
        fetch_rows: list[tuple[Any, ...]],
        fetchone_results: list[tuple[Any, ...] | None] | None = None,
    ) -> None:
        self.executes: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_rows = fetch_rows
        self._fetchone_results = list(fetchone_results or [])

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = (), *, timeout: int) -> None:
        self.executes.append((sql, params))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._fetch_rows

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def cursor(self) -> _Cursor:
        return self._cursor


def _observation_row() -> tuple[Any, ...]:
    return (
        "obs-1",
        "population_2022",
        "01003",
        date(2018, 1, 1),
        date(2022, 12, 31),
        "PERIOD",
        "233420",
        "OBSERVED",
        "people",
        "NONE",
        "rel-1",
        "context_svi",
        "CDC/ATSDR Social Vulnerability Index",
        "2022 (2018-2022 ACS)",
        "https://example.test",
        datetime(2026, 1, 1),
        "svi_numeric_projection_v2",
        "semantic-2.0.0",
        "[]",
        "[]",
        "cdc_atsdr_svi",
        "atsdr-svi-2022-county-layer",
        "1.0.0",
        "meta-rev",
        "semantic-1.0.0",
        2022,
        "integer",
        None,
        "E_TOTPOP",
        "definition",
        '["OBSERVED","ZERO","MISSING"]',
        '[{"text":"limit"}]',
        None,
        CONTEXT_CONTRACT,
    )


def test_query_context_success_one_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    obs = _observation_row()
    cursor = _Cursor(fetch_rows=[("rel-1", *obs)])
    connection = _Connection(cursor)
    monkeypatch.setattr(
        "lyme_gap_atlas_api.snowflake_read_session.connect",
        lambda _: connection,
    )
    repo = SnowflakeObservationRepository(ApiSettings(), _connection=connection)
    release, rows = repo.query_context(_query(), 0)
    assert release == "rel-1"
    assert rows == [obs]
    assert len(cursor.executes) == 1


def test_search_empty_selection_second_execute_for_measure_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(fetch_rows=[("rel-1", *([None] * len(FIELDS)))])
    connection = _Connection(cursor)
    monkeypatch.setattr(
        "lyme_gap_atlas_api.snowflake_read_session.connect",
        lambda _: connection,
    )
    exists_calls = 0

    class MeasuringRepository(SnowflakeObservationRepository):
        def measure_exists(self, _mid: str, _release: str) -> bool:
            nonlocal exists_calls
            exists_calls += 1
            return False

    repo = MeasuringRepository(ApiSettings(), _connection=connection)
    service = ObservationService(repo)
    with pytest.raises(PublicQueryError, match="Measure has no published"):
        service.search(_query())
    assert len(cursor.executes) == 1
    assert exists_calls == 1


def test_search_empty_selection_runs_measure_exists_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(
        fetch_rows=[("rel-1", *([None] * len(FIELDS)))],
        fetchone_results=[None],
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(
        "lyme_gap_atlas_api.snowflake_read_session.connect",
        lambda _: connection,
    )
    repo = SnowflakeObservationRepository(ApiSettings(), _connection=connection)
    service = ObservationService(repo)
    with pytest.raises(PublicQueryError, match="Measure has no published"):
        service.search(_query())
    assert len(cursor.executes) == 2
    assert "LIMIT 1" in cursor.executes[1][0]


def test_search_admitted_empty_context_message(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _Cursor(fetch_rows=[("rel-1", *([None] * len(FIELDS)))])
    connection = _Connection(cursor)
    monkeypatch.setattr(
        "lyme_gap_atlas_api.snowflake_read_session.connect",
        lambda _: connection,
    )

    class ExistsRepository(SnowflakeObservationRepository):
        def measure_exists(self, _mid: str, _release: str) -> bool:
            return True

    repo = ExistsRepository(ApiSettings(), _connection=connection)
    service = ObservationService(repo)
    with pytest.raises(PublicQueryError, match="No admitted county context"):
        service.search(_query())
    assert len(cursor.executes) == 1


def test_search_admitted_empty_context_second_sql_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = _Cursor(
        fetch_rows=[("rel-1", *([None] * len(FIELDS)))],
        fetchone_results=[(1,)],
    )
    connection = _Connection(cursor)
    monkeypatch.setattr(
        "lyme_gap_atlas_api.snowflake_read_session.connect",
        lambda _: connection,
    )
    repo = SnowflakeObservationRepository(ApiSettings(), _connection=connection)
    service = ObservationService(repo)
    with pytest.raises(PublicQueryError, match="No admitted county context"):
        service.search(_query())
    assert len(cursor.executes) == 2
