"""Offline connector lifecycle proof; not production latency evidence."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient
from test_environmental_context import fixture_row

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.environmental_context import FIELDS
from lyme_gap_atlas_api.public_contract import GeographyType, ObservationQuery, PublicQueryError
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


def query(measure="prcp"):
    return ObservationQuery(
        measure_id=f"nclimgrid_{measure}_county_day",
        geography_type=GeographyType.county,
        geography_id=["01001"],
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 1),
    )


class Session:
    def __init__(self, *, rows=None, fail=None, pointer_changes=False, barrier=None):
        self.rows = [fixture_row()] if rows is None else rows
        self.fail = fail
        self.pointer_changes = pointer_changes
        self.barrier = barrier
        self.calls = []
        self.cursor_closes = 0
        self.closes = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closes += 1

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True
        self.closes += 1

    def cursor(self):
        return Cursor(self)


class Cursor:
    def __init__(self, session):
        self.session = session
        self.sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.cursor_closes += 1

    def execute(self, sql, params=(), *, timeout):
        self.sql = sql
        self.session.calls.append((sql, params, timeout))
        if self.session.fail == f"execute{len(self.session.calls)}":
            raise RuntimeError("private driver error")
        if self.session.barrier and len(self.session.calls) == 1:
            self.session.barrier.wait(timeout=5)

    def fetchone(self):
        if self.session.fail == "release_fetch":
            raise RuntimeError("private release error")
        return ("fixture-release",)

    def fetchmany(self, limit):
        assert limit == 2
        return [self.fetchone()]

    def fetchall(self):
        if self.session.fail == "fetch":
            raise RuntimeError("private fetch error")
        if "CURRENT_CLIMATE_MEASURE_METADATA_V" in self.sql:
            return [(1,)]
        return [] if self.session.pointer_changes else self.session.rows


def setup_repository(monkeypatch, factory):
    settings = ApiSettings(environmental_context_enabled=True, public_query_timeout_seconds=5)
    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", factory)
    return SnowflakeObservationRepository(settings)


def test_one_session_three_bounded_queries_and_cleanup(monkeypatch):
    session = Session()
    connects = []
    repository = setup_repository(
        monkeypatch, lambda settings: connects.append(settings) or session
    )
    result = ObservationService(repository).search(query())
    assert result.data[0].value == 0
    assert len(connects) == 1
    assert len(session.calls) == session.cursor_closes == 3
    assert session.closes == 0
    assert repository._connection is None
    assert all(timeout == 5 for _, _, timeout in session.calls)
    assert "CURRENT_RELEASE_V" in session.calls[0][0]
    assert session.calls[1][1] == (query().measure_id, "fixture-release")
    sql, binds, _ = session.calls[2]
    assert "CURRENT_CLIMATE_COUNTY_DAY_OBSERVATIONS_V" in sql
    assert "release_id=%s" in sql
    assert binds == (
        query().measure_id,
        "01001",
        date(2025, 1, 1),
        date(2025, 1, 1),
        "fixture-release",
        101,
        0,
    )


@pytest.mark.parametrize("failure", ["execute1", "execute2", "execute3", "release_fetch", "fetch"])
def test_query_or_fetch_failure_closes_owned_session(monkeypatch, failure):
    session = Session(fail=failure)
    repository = setup_repository(monkeypatch, lambda settings: session)
    with pytest.raises(AtlasDataUnavailableError) as error:
        ObservationService(repository).search(query())
    assert "private" not in str(error.value)
    assert session.closes == 1
    assert session.cursor_closes == len(session.calls)
    assert repository._connection is None


def test_mapper_failure_and_following_request_do_not_leak_session(monkeypatch):
    sessions = [Session(rows=[("invalid-shape",)]), Session()]
    allocated = []

    def connect(settings):
        session = sessions[len(allocated)]
        allocated.append(session)
        return session

    repository = setup_repository(monkeypatch, connect)
    service = ObservationService(repository)
    with pytest.raises(AtlasDataUnavailableError):
        service.search(query())
    assert service.search(query()).data[0].value == 0
    assert all(session.closes == 1 and session.cursor_closes == 3 for session in sessions[:1])
    assert sessions[1].closes == 0


def test_concurrent_requests_own_distinct_sessions(monkeypatch):
    barrier = Barrier(2)
    lock = Lock()
    sessions = []

    def connect(settings):
        session = Session(barrier=barrier)
        with lock:
            sessions.append(session)
        return session

    repository = setup_repository(monkeypatch, connect)
    service = ObservationService(repository)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.search(query()), range(2)))
    assert all(result.data[0].value == 0 for result in results)
    assert len(sessions) == 2
    assert all(
        session.closes == 0 and len(session.calls) == session.cursor_closes == 3
        for session in sessions
    )
    assert repository._connection is None


def test_pointer_change_cannot_return_old_release_observations(monkeypatch):
    session = Session(pointer_changes=True)
    repository = setup_repository(monkeypatch, lambda settings: session)
    assert ObservationService(repository).search(query()).data == []
    assert session.calls[1][1][-1] == "fixture-release"
    assert session.calls[2][1][-3] == "fixture-release"
    assert "release_id=%s" in session.calls[1][0] and "release_id=%s" in session.calls[2][0]
    assert session.closes == 0


@pytest.mark.parametrize("measure", ["prcp", "tmin", "tmax", "tavg"])
@pytest.mark.parametrize("state", ["present", "out_of_coverage", "no_data"])
def test_real_adapter_http_scientific_contract_with_one_session(monkeypatch, measure, state):
    unavailable = state == "out_of_coverage"
    row = list(
        fixture_row(
            value=None if unavailable else (0.0 if measure == "prcp" else -2.5),
            state="UNAVAILABLE" if unavailable else ("ZERO" if measure == "prcp" else "OBSERVED"),
            coverage="OUT_OF_SOURCE_COVERAGE" if unavailable else "COMPLETE",
        )
    )
    row[FIELDS.index("measure_id")] = query(measure).measure_id
    row[FIELDS.index("unit")] = "mm" if measure == "prcp" else "degree_Celsius"
    session = Session(rows=[] if state == "no_data" else [tuple(row)])
    repository = setup_repository(monkeypatch, lambda settings: session)
    client = TestClient(create_app(settings=repository.settings, observation_repository=repository))
    response = client.get(
        "/v1/observations",
        params={
            "measure_id": query(measure).measure_id,
            "geography_type": "county",
            "geography_id": "01001",
            "start_date": "2025-01-01",
            "end_date": "2025-01-01",
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    if state == "no_data":
        assert data == []
        assert response.json()["meta"]["reason"] == "NO_PUBLISHED_RESULTS"
        assert response.headers["cache-control"] == "no-store"
        assert "CURRENT_RELEASE_V" in session.calls[3][0]
    else:
        assert data[0]["value"] == (None if unavailable else (0 if measure == "prcp" else -2.5))
        assert data[0]["measure_id"] == query(measure).measure_id
        assert data[0]["release_id"] == "fixture-release"
        assert data[0]["period_start"] == data[0]["period_end"] == "2025-01-01"
        assert data[0]["environmental_context"]["coverage_status"] == (
            "OUT_OF_SOURCE_COVERAGE" if unavailable else "COMPLETE"
        )
        assert "artifact_id" not in data[0]
    expected_queries = 4 if state == "no_data" else 3
    assert session.closes == 0 and len(session.calls) == session.cursor_closes == expected_queries


def test_simulated_handshake_budget_compares_identical_queries(monkeypatch):
    """Counter model only: 100ms/query + 1000ms/connect, not measured latency."""
    settings = ApiSettings(environmental_context_enabled=True, public_query_timeout_seconds=5)
    sessions = []

    def connect(settings):
        session = Session()
        sessions.append(session)
        return session

    monkeypatch.setattr("lyme_gap_atlas_api.snowflake_read_session.connect", connect)
    repository = SnowflakeObservationRepository(settings)
    # Un-borrowed adapter calls now lend from the shared pool: one login, kept open.
    release = repository.current_release()
    assert repository.measure_exists(query().measure_id, release)
    baseline_rows = repository.query(query(), release, 0)
    baseline_calls = [call for session in sessions for call in session.calls]
    baseline_connects = len(sessions)
    baseline_simulated_ms = baseline_connects * 1000 + len(baseline_calls) * 100
    assert all(session.closes == 0 for session in sessions)
    get_read_session_pool().close_all()
    sessions.clear()
    response = ObservationService(repository).search(query())
    candidate_calls = [call for session in sessions for call in session.calls]
    candidate_simulated_ms = len(sessions) * 1000 + len(candidate_calls) * 100
    assert baseline_rows == [fixture_row()]
    assert response.data[0].value == 0
    assert baseline_calls == candidate_calls
    assert (baseline_connects, len(sessions)) == (1, 1)
    assert (baseline_simulated_ms, candidate_simulated_ms) == (1300, 1300)
    assert sessions[0].closes == 0


def test_connection_failure_is_sanitized_and_does_not_persist(monkeypatch):
    def unavailable(settings):
        raise RuntimeError("private login detail")

    repository = setup_repository(monkeypatch, unavailable)
    with pytest.raises(AtlasDataUnavailableError) as error:
        ObservationService(repository).search(query())
    assert "private" not in str(error.value)
    assert repository._connection is None


def test_expired_deadline_does_not_acquire_session(monkeypatch):
    connects = []
    repository = setup_repository(monkeypatch, lambda settings: connects.append(settings))

    def expired():
        raise AtlasDataUnavailableError("Request deadline expired")

    with pytest.raises(AtlasDataUnavailableError, match="deadline"):
        ObservationService(repository).search(query(), checkpoint=expired)
    assert connects == []


def test_later_deadline_checkpoint_closes_session(monkeypatch):
    session = Session()
    repository = setup_repository(monkeypatch, lambda settings: session)
    checks = []

    def expired_later():
        checks.append(True)
        if len(checks) == 2:
            raise AtlasDataUnavailableError("Request deadline expired")

    with pytest.raises(AtlasDataUnavailableError, match="deadline"):
        ObservationService(repository).search(query(), checkpoint=expired_later)
    assert session.closes == 1


class BrokenTeardownSession(Session):
    def __exit__(self, *args):
        super().__exit__(*args)
        raise RuntimeError("private teardown detail")


def test_successful_search_keeps_pooled_connection_open(monkeypatch):
    session = BrokenTeardownSession()
    repository = setup_repository(monkeypatch, lambda settings: session)
    result = ObservationService(repository).search(query())
    assert result.data[0].value == 0
    assert session.closes == 0
    assert len(session.calls) == session.cursor_closes == 3


@pytest.mark.parametrize(
    "body_error",
    [
        AtlasDataUnavailableError("Original sanitized failure"),
        PublicQueryError("INVALID_REQUEST", "Original public error"),
    ],
)
def test_body_error_survives_simultaneous_teardown_failure(monkeypatch, body_error):
    session = BrokenTeardownSession()
    repository = setup_repository(monkeypatch, lambda settings: session)
    with pytest.raises(type(body_error)) as error, repository.climate_request():
        raise body_error
    assert error.value is body_error
    assert session.closes == 1


@pytest.mark.parametrize("session_class", [Session, BrokenTeardownSession])
def test_cancelled_body_propagates_and_closes_session(monkeypatch, session_class):
    session = session_class()
    repository = setup_repository(monkeypatch, lambda settings: session)
    cancelled = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError) as error, repository.climate_request():
        raise cancelled
    assert error.value is cancelled
    assert session.closes == 1
    assert session.calls == []
