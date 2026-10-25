import logging
import threading
import time

import pytest
from fastapi.testclient import TestClient
from test_api import FakeRepository

from lyme_gap_atlas_api import app as app_module
from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.repository import AtlasDataUnavailableError, Snapshot
from lyme_gap_atlas_api.service import AtlasService

TTL = 300
MAX_STALE = 600
BACKOFF = 30


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class FlakyRepository(FakeRepository):
    def __init__(self) -> None:
        self.loads = 0
        self.fail = False
        self.delay = 0.0

    def load_snapshot(self) -> Snapshot:
        self.loads += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("secret upstream detail")
        return super().load_snapshot()


def build() -> tuple[AtlasService, FlakyRepository, Clock]:
    repository, clock = FlakyRepository(), Clock()
    service = AtlasService(
        repository,
        TTL,
        max_stale_seconds=MAX_STALE,
        refresh_backoff_seconds=BACKOFF,
        clock=clock,
    )
    return service, repository, clock


def test_refresh_failure_after_ttl_serves_prior_snapshot_without_retry_during_backoff() -> None:
    service, repository, clock = build()
    first = service.snapshot()
    clock.now += TTL
    repository.fail = True

    assert service.snapshot() is first
    assert service.is_stale
    assert repository.loads == 2

    clock.now += BACKOFF - 1
    assert service.snapshot() is first
    assert repository.loads == 2

    clock.now += 1
    assert service.snapshot() is first
    assert repository.loads == 3


def test_snapshot_older_than_max_stale_age_raises_existing_unavailable_error() -> None:
    service, repository, clock = build()
    service.snapshot()
    repository.fail = True
    clock.now += TTL + MAX_STALE - 1
    service.snapshot()
    clock.now += 1

    with pytest.raises(AtlasDataUnavailableError, match="unavailable"):
        service.snapshot()
    # Backed-off requests past the bound fail fast without another load.
    loads = repository.loads
    with pytest.raises(AtlasDataUnavailableError):
        service.snapshot()
    assert repository.loads == loads


def test_no_snapshot_yet_fails_with_backoff() -> None:
    service, repository, clock = build()
    repository.fail = True

    with pytest.raises(AtlasDataUnavailableError):
        service.snapshot()
    with pytest.raises(AtlasDataUnavailableError):
        service.snapshot()
    assert repository.loads == 1
    clock.now += BACKOFF
    repository.fail = False
    assert service.snapshot().metadata.release_id == "alpha-2026-08-06"
    assert not service.is_stale


def test_recovery_resumes_normal_refresh(caplog: pytest.LogCaptureFixture) -> None:
    service, repository, clock = build()
    first = service.snapshot()
    clock.now += TTL
    repository.fail = True
    with caplog.at_level(logging.INFO, logger="lyme_gap_atlas_api.service"):
        service.snapshot()
        clock.now += BACKOFF
        service.snapshot()
        repository.fail = False
        clock.now += BACKOFF
        recovered = service.snapshot()
        service.snapshot()

    assert recovered is not first
    assert not service.is_stale
    messages = [record.getMessage() for record in caplog.records]
    assert messages.count("atlas_snapshot_refresh_degraded") == 1
    assert messages.count("atlas_snapshot_refresh_recovered") == 1
    assert "secret upstream detail" not in caplog.text
    clock.now += TTL
    service.snapshot()
    assert repository.loads == 5


def test_concurrent_callers_cause_a_single_load_and_a_single_failed_refresh() -> None:
    service, repository, clock = build()
    repository.delay = 0.05
    results: list[Snapshot] = []

    def call() -> None:
        results.append(service.snapshot())

    threads = [
        threading.Thread(target=lambda: results.append(service.snapshot())) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert repository.loads == 1
    assert len({id(item) for item in results}) == 1

    clock.now += TTL
    repository.fail = True
    results.clear()
    threads = [
        threading.Thread(target=lambda: results.append(service.snapshot())) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert repository.loads == 2
    assert len(results) == 8


def test_stale_snapshot_is_not_cached_publicly_and_keeps_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, clock = FlakyRepository(), Clock()
    settings = ApiSettings(
        snowflake_account="test",
        snowflake_user="test",
        snowflake_role="test",
        snowflake_pat="test",
        cors_origins=["https://carawaylabs.com"],
        rate_limit_per_minute=100,
        cache_ttl_seconds=TTL,
        snapshot_max_stale_seconds=MAX_STALE,
    )
    monkeypatch.setattr(
        app_module,
        "AtlasService",
        lambda repo, ttl, **kwargs: AtlasService(repo, ttl, **{**kwargs, "clock": clock}),
    )
    api = TestClient(create_app(repository, settings))

    fresh = api.get("/v1/atlas/metadata")
    assert fresh.headers["cache-control"] == "public, max-age=300"

    clock.now += TTL
    repository.fail = True
    stale = api.get("/v1/atlas/metadata")

    assert stale.status_code == 200
    assert stale.headers["cache-control"] == "private, no-store"
    assert stale.json() == fresh.json()

    clock.now += MAX_STALE
    gone = api.get("/v1/atlas/metadata")
    assert gone.status_code == 503
