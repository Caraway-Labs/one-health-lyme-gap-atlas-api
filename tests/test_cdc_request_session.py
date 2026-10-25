"""Request ownership and I/O equivalence with a controlled SDK, never live data."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient
from lyme_gap_atlas_data import cdc_consumer_batch as batch_runtime
from lyme_gap_atlas_data import cdc_consumer_runtime as runtime
from test_cdc_batch import envelope
from test_cdc_surveillance import params, settings

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.cdc_surveillance import IDENTITIES, RELEASE, CDCObservationAdapter
from lyme_gap_atlas_api.cdc_timing import CDCTiming, cdc_request_timing
from lyme_gap_atlas_api.public_contract import ObservationQuery, PublicQueryError
from lyme_gap_atlas_api.public_observations import (
    ObservationService,
    SnowflakeObservationRepository,
)
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError
from lyme_gap_atlas_api.snowflake_read_session import get_read_session_pool
from lyme_gap_atlas_api.snowflake_session_baseline import IDENTITY_SQL as POOL_IDENTITY_SQL
from lyme_gap_atlas_api.snowflake_session_baseline import baseline_statements


@pytest.fixture(autouse=True)
def _isolated_read_pool() -> None:
    get_read_session_pool().close_all()
    yield
    get_read_session_pool().close_all()


REAL_SESSION_BASELINE = True
SETUP_SQL = baseline_statements(settings(public_query_timeout_seconds=5)) + (POOL_IDENTITY_SQL,)


class Session:
    def __init__(self, fail=None, barrier=None, release=RELEASE):
        self.calls = []  # per-request statements only
        self.setup_calls = []  # pool baseline + identity assertion, once per new connection
        self.cursor_closes = 0
        self.closes = 0
        self.closed = False
        self.fail = fail
        self.barrier = barrier
        self.release = release

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        if not self.closed:
            self.closed = True
        self.closes += 1

    def cursor(self):
        return Cursor(self)


class Cursor:
    def __init__(self, session):
        self.session = session
        self.description = []
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.session.cursor_closes += 1

    def execute(self, sql, params=(), *, timeout):
        s = self.session
        if sql in SETUP_SQL:
            s.setup_calls.append((sql, params, timeout))
            if sql == POOL_IDENTITY_SQL:
                self.rows = [
                    (
                        "OH_LYME_API_SVC",
                        "OH_LYME_PROD_READ",
                        "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
                        "COMPUTE_WH",
                    )
                ]
                self.setup_identity = True
            return
        self.setup_identity = False
        s.calls.append((sql, params, timeout))
        if s.barrier and len(s.calls) == 1:
            s.barrier.wait(timeout=5)
        if s.fail == len(s.calls):
            raise TimeoutError("private SQL credentials")
        if sql == runtime.IDENTITY_SQL:
            self.description = [(x,) for x in ["USER", "ROLE", "DATABASE", "WAREHOUSE"]]
            self.rows = [
                (
                    "OH_LYME_API_SVC",
                    "OH_LYME_PROD_READ",
                    "ONE_HEALTH_LYME_GAP_ATLAS_PROD",
                    "COMPUTE_WH",
                )
            ]
        elif sql == runtime.AUTHORITY_SQL:
            self.description = [("synthetic",)]
            self.rows = [(params[2],)]
        elif sql.startswith("WITH scoped"):
            self.description = [("synthetic",)]
            self.rows = [("batch",)]
        elif "CURRENT_RELEASE_V" in sql:
            self.rows = [(s.release,)]

    def fetchone(self):
        if getattr(self, "setup_identity", False):
            return self.rows[0]
        return (self.session.release,)

    def fetchmany(self, limit):
        assert limit == 2
        return self.rows[:limit]


def controlled_project(query, root, measure, county, environment, *, release_id):
    """Test only: drive Data's existing query callback; scientific tests are separate."""
    assert environment == "prod" and release_id == RELEASE
    identity = query(runtime.IDENTITY_SQL, ())
    assert identity[0]["ROLE"] == "OH_LYME_PROD_READ"
    rows = query(runtime.AUTHORITY_SQL, (RELEASE, measure, county, IDENTITIES[measure][1]))
    assert rows == [{"synthetic": county}]
    return envelope(measure, county)


def service(monkeypatch, factory, *, project=controlled_project):
    monkeypatch.setattr(
        "lyme_gap_atlas_api.dependency_telemetry.snowflake_connect",
        lambda config, **kwargs: factory(config),
    )
    if project is not None:
        monkeypatch.setattr(runtime, "_project", project)

        def controlled_batch(query, measure, counties, *, environment, release_id):
            identity = query(runtime.IDENTITY_SQL, ())
            assert identity[0]["ROLE"] == "OH_LYME_PROD_READ"
            query(
                batch_runtime._authority_sql(len(counties)),
                (RELEASE, measure, IDENTITIES[measure][1], *counties),
            )

            def local_query(sql, params):
                return identity if sql == runtime.IDENTITY_SQL else [{"synthetic": params[2]}]

            return batch_runtime.CDCBatchResultV1(
                measure,
                release_id,
                counties,
                tuple(
                    project(local_query, None, measure, county, environment, release_id=release_id)
                    for county in counties
                ),
            )

        monkeypatch.setattr(batch_runtime, "produce_cdc_batch_v1", controlled_batch)
    config = settings(cdc_surveillance_enabled=True, public_query_timeout_seconds=5)
    repository = SnowflakeObservationRepository(config)
    return ObservationService(repository, cdc_adapter=CDCObservationAdapter(config))


@pytest.mark.parametrize("measure", list(IDENTITIES))
@pytest.mark.parametrize("n", [1, 2, 5, 10, 50, 51])
def test_single_session_setup_per_request_and_per_county_identity(monkeypatch, measure, n):
    s = Session()
    opened = []
    svc = service(monkeypatch, lambda config: opened.append(config) or s)
    counties = [f"{i:05}" for i in range(1, n + 1)]
    recorder = CDCTiming()
    with cdc_request_timing(recorder):
        response = svc.search(ObservationQuery(**params(measure, counties, page_size=100)))
    assert recorder.serving_path == ("batch" if n <= 50 else "singleton")
    assert recorder.processed_counties == n
    assert recorder.counts["authority_query"] == (1 if n <= 50 else n)
    assert len(response.data) == n
    assert len(opened) == 1 and s.closes == 0
    assert len(s.calls) == (3 if n <= 50 else 1 + 2 * n)
    assert s.cursor_closes == (
        3 if n <= 50 else 2 + n
    )  # setup, release, then one cursor per county
    # The baseline ran once, at pool creation; the request issued no ALTER/USE of its own.
    assert s.setup_calls == [
        ("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS=5", (), 5),
        ("USE SECONDARY ROLES NONE", (), 5),
        (POOL_IDENTITY_SQL, (), 5),
    ]
    assert not any(c[0].startswith(("ALTER SESSION", "USE ")) for c in s.calls)
    assert "CURRENT_RELEASE_V" in s.calls[0][0]
    if n <= 50:
        assert s.calls[1][0] == runtime.IDENTITY_SQL
        assert s.calls[2][0] == batch_runtime._authority_sql(n)
        assert s.calls[2][1][3:] == tuple(counties)
    else:
        assert [c[0] for c in s.calls[1:]] == [runtime.IDENTITY_SQL, runtime.AUTHORITY_SQL] * n
        assert [c[1][2] for c in s.calls if c[0] == runtime.AUTHORITY_SQL] == counties
    assert all(c[2] == 5 for c in s.calls + s.setup_calls)
    assert svc.repository._connection is None
    assert svc.cdc_adapter._producer_override is None


@pytest.mark.parametrize("failure", range(1, 4))
def test_setup_release_identity_authority_timeout_closes_without_partial_results(
    monkeypatch, failure
):
    s = Session(fail=failure)
    svc = service(monkeypatch, lambda _: s)
    with pytest.raises(AtlasDataUnavailableError):
        svc.search(ObservationQuery(**params(county=["01001", "13081"])))
    assert s.closes == 1
    assert s.cursor_closes >= 1
    assert svc.repository._connection is None


@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError, KeyboardInterrupt])
def test_checkpoint_or_producer_cancellation_closes_session(monkeypatch, failure):
    s = Session()
    svc = service(monkeypatch, lambda _: s)
    calls = [0]

    def checkpoint():
        calls[0] += 1
        if calls[0] == 5:
            raise failure("private cancellation")

    with pytest.raises(failure):
        svc.search(ObservationQuery(**params(county=["01001", "13081"])), checkpoint=checkpoint)
    assert s.closes == 1


def test_preflight_before_connection_and_wrong_release_cleanup(monkeypatch):
    s = Session(release="unreviewed")
    opened = []
    svc = service(monkeypatch, lambda _: opened.append(s) or s)
    with pytest.raises(PublicQueryError):
        svc.search(
            ObservationQuery(
                **(params(county=["13081"]) | {"year": 2023, "start_date": None, "end_date": None})
            )
        )
    assert not opened
    with pytest.raises(AtlasDataUnavailableError):
        svc.search(ObservationQuery(**params(county=["13081"])))
    # Our own domain error after completed statements keeps the pooled session.
    assert s.closes == 0 and len(s.calls) == 1


@pytest.mark.parametrize(
    "key,value",
    [
        ("snowflake_role", "ACCOUNTADMIN"),
        ("snowflake_database", "OTHER"),
        ("snowflake_warehouse", "OTHER"),
        ("snowflake_user", "OTHER"),
    ],
)
def test_invalid_runtime_identity_never_connects(monkeypatch, key, value):
    svc = service(monkeypatch, lambda _: pytest.fail("invalid identity connected"))
    svc.cdc_adapter.settings = svc.cdc_adapter.settings.model_copy(update={key: value})
    with pytest.raises(AtlasDataUnavailableError):
        svc.search(ObservationQuery(**params(county=["13081"])))


def test_real_data_producer_still_rejects_missing_authority(monkeypatch):
    s = Session()
    svc = service(monkeypatch, lambda _: s, project=None)
    with pytest.raises(AtlasDataUnavailableError):
        svc.search(ObservationQuery(**params(county=["13081"])))
    assert s.closes == 1
    assert s.calls[-1][0] == batch_runtime._authority_sql(1)


def test_overlapping_requests_use_distinct_sessions(monkeypatch):
    barrier, lock, sessions = Barrier(2), Lock(), []

    def connect(_):
        s = Session(barrier=barrier)
        with lock:
            sessions.append(s)
        return s

    svc = service(monkeypatch, connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: svc.search(ObservationQuery(**params(county=["13081"]))), range(2))
        )
    assert len(sessions) == 2 and sessions[0] is not sessions[1]
    assert all(s.closes == 0 and len(s.calls) == 3 for s in sessions)
    assert results[0].data == results[1].data


@pytest.mark.parametrize("measure", list(IDENTITIES))
def test_http_pagination_and_unsampled_safe_timing(monkeypatch, caplog, measure):
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    sessions = []

    def connect(_):
        s = Session()
        sessions.append(s)
        return s

    svc = service(monkeypatch, connect)
    app = create_app(settings=svc.repository.settings, observation_repository=svc.repository)
    app.state.observation_service = svc
    client = TestClient(app)
    counties = ["12047", "19135"] if measure == "burgdorferi_status" else ["01001", "13081"]
    query = params(measure, counties, page_size=1)
    with caplog.at_level(logging.INFO):
        first = client.get("/v1/observations", params=query)
        assert first.status_code == 200
        query["page_token"] = first.json()["meta"]["next_page_token"]
        second = client.get("/v1/observations", params=query)
    assert second.status_code == 200
    items = [first.json()["data"][0], second.json()["data"][0]]
    assert [x["geography"]["geography_id"] for x in items] == counties
    if measure == "burgdorferi_status":
        assert items[0]["value_state"] == "NO_RECORDS"
        assert items[0]["surveillance_context"]["evidence"]["normalized_state"] == "unknown"
    assert second.json()["meta"]["next_page_token"] is None
    assert sum(len(s.calls) for s in sessions) == 6
    if len(sessions) == 1:
        assert len(sessions[0].calls) == 6
    else:
        assert [len(s.calls) for s in sessions] == [3, 3]
    assert all(len(s.setup_calls) == 3 for s in sessions)  # baseline once per connection
    assert all(s.closes == 0 for s in sessions)
    completions = [
        r.context
        for r in caplog.records
        if r.message == "api_request_completed"
        and getattr(r, "context", {}).get("path") == "/v1/observations"
    ]
    assert len(completions) == 2
    assert sum(e["cdc_operation_counts"].get("connect", 0) for e in completions) >= 1
    for event, processed in zip(completions, [2, 1], strict=True):
        assert event["cdc_serving_path"] == "batch"
        assert event["cdc_processed_counties"] == processed
        counts = event["cdc_operation_counts"]
        assert counts.get("connect", 0) in {0, 1}
        assert counts.get("connection_teardown", 0) == 0
        # 3 request statements, plus the 3 baseline statements if this request connected
        assert counts["execute"] == 3 + 3 * counts.get("connect", 0)
        assert counts["fetchmany"] == 3
        assert event["cdc_timing_ms"]["service"] >= event["cdc_timing_ms"]["producer"]
        assert event["cdc_timing_ms"]["producer_processing"] >= 0
        text = json.dumps(event)
        for secret in ["12047", "19135", "SELECT", "ALTER", "OH_LYME", "synthetic"]:
            assert secret not in text


def test_http_failure_retains_timings_and_does_not_return_partial_success(monkeypatch, caplog):
    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    s = Session(fail=3)  # the single batch authority read
    svc = service(monkeypatch, lambda _: s)
    app = create_app(settings=svc.repository.settings, observation_repository=svc.repository)
    app.state.observation_service = svc
    with caplog.at_level(logging.INFO):
        response = TestClient(app).get("/v1/observations", params=params(county=["01001", "13081"]))
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert "data" not in response.json() and "private" not in response.text
    event = next(r.context for r in caplog.records if r.message == "api_request_completed")
    assert event["cdc_operation_counts"]["execute"] == 6  # 3 baseline + 3 request
    counts = event["cdc_operation_counts"]
    assert counts.get("close", counts.get("connection_teardown", 0)) == 1
    assert event["cdc_timing_ms"]["service"] > 0
    assert s.closes == 1


@pytest.mark.parametrize(
    "body_failure", [None, TimeoutError, asyncio.CancelledError, KeyboardInterrupt]
)
def test_sdk_transaction_teardown_error_still_closes_and_preserves_body(monkeypatch, body_failure):
    class BrokenTeardown(Session):
        def __exit__(self, *args):
            raise RuntimeError("private implicit COMMIT/ROLLBACK error")

        def close(self):
            self.closes += 1

    s = BrokenTeardown()
    original = body_failure("original body error") if body_failure else None

    def producer(*args, **kwargs):
        if original is not None:
            raise original
        return controlled_project(*args, **kwargs)

    svc = service(monkeypatch, lambda _: s, project=producer)
    if body_failure is None:
        response = svc.search(ObservationQuery(**params(county=["13081"])))
        assert response.data
        assert s.closes == 0
        return
    expected = (
        body_failure
        if body_failure in {asyncio.CancelledError, KeyboardInterrupt}
        else AtlasDataUnavailableError
    )
    with pytest.raises(expected) as captured:
        svc.search(ObservationQuery(**params(county=["13081"])))
    assert s.closes == 1
    if body_failure in {asyncio.CancelledError, KeyboardInterrupt}:
        assert captured.value is original
    else:
        assert "private" not in str(captured.value)


@pytest.mark.parametrize("operation", ["execute", "fetchmany"])
def test_fetch_error_closes_session(monkeypatch, operation):
    def fail(*args, **kwargs):
        raise TimeoutError("private result chunk")

    monkeypatch.setattr(Cursor, operation, fail)
    s = Session()
    svc = service(monkeypatch, lambda _: s)
    with pytest.raises(AtlasDataUnavailableError):
        svc.search(ObservationQuery(**params(county=["13081"])))
    assert s.closes == 1


@pytest.mark.parametrize("field", ["metadata", "release", "county", "private"])
def test_second_county_admission_failure_is_atomic_and_closes(monkeypatch, field):
    def project(*args, **kwargs):
        result = controlled_project(*args, **kwargs)
        if args[3] == "13081":
            if field == "metadata":
                result["semantic"]["measure"]["metadata_revision"] = 2
            elif field == "release":
                result["semantic"]["release"]["id"] = "old-unadmitted-release"
            elif field == "county":
                result["semantic"]["observation"]["geography"]["county_fips"] = "99999"
            else:
                result["semantic"]["lineage"]["private_authority"] = "restricted"
        return result

    s = Session()
    svc = service(monkeypatch, lambda _: s, project=project)
    with pytest.raises(AtlasDataUnavailableError):
        svc.search(ObservationQuery(**params(county=["01001", "13081"])))
    assert s.closes == 0  # domain error from our own code: session stays pooled


@pytest.mark.parametrize("column,value", [("ROLE", "ACCOUNTADMIN"), ("DATABASE", "OTHER")])
def test_real_data_identity_check_remains_mandatory(monkeypatch, column, value):
    original = Cursor.execute

    def execute(self, sql, *args, **kwargs):
        original(self, sql, *args, **kwargs)
        if sql == runtime.IDENTITY_SQL:
            columns = [x[0] for x in self.description]
            row = list(self.rows[0])
            row[columns.index(column)] = value
            self.rows = [tuple(row)]

    monkeypatch.setattr(Cursor, "execute", execute)
    s = Session()
    svc = service(monkeypatch, lambda _: s, project=None)
    with pytest.raises(AtlasDataUnavailableError):
        svc.search(ObservationQuery(**params(county=["13081"])))
    assert s.calls[-1][0] == runtime.IDENTITY_SQL
    assert s.closes == 1
