"""Repeatable Snowflake connection benchmark: pooled vs a "no pool" baseline (API #326).

Run from the repository (no credentials, no network, never production evidence):

    uv run python scripts/benchmark_snowflake_connections.py
    uv run python scripts/benchmark_snowflake_connections.py --requests 400 --concurrency 32 \
        --login-ms 25 --format markdown

By default every Snowflake connection is a fake whose login and statement cost are
simulated sleeps (``--login-ms``, ``--work-ms``). Two modes are compared per scenario:

* ``no_pool``: every borrow logs in and closes, the pre-#316 behaviour (1 login per borrow);
* ``pooled``: the real ``ReadSessionPool`` / ``WriteSessionPool`` (the same code the API
  runs) over the same fakes, so logins per call, checkout wait p50/p95 and exhaustion
  counts come from the shipped implementation.

Scenarios model the public read paths and the feedback / Ask Atlas write borrow patterns.

Opt-in staging run (``--staging``): uses the real pools and connector against the
configured Snowflake environment with a trivial ``SELECT 1`` as the work. It refuses to
run unless ``ATLAS_BENCHMARK_ALLOW_STAGING=1`` and ``ATLAS_BENCHMARK_CONFIRM_DATABASE``
equals the configured database name exactly, and it refuses any database, role or
warehouse that looks like production or an admin role. It is never run by tests or CI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lyme_gap_atlas_api import snowflake_read_session as read_module  # noqa: E402
from lyme_gap_atlas_api import snowflake_write_session as write_module  # noqa: E402
from lyme_gap_atlas_api.config import ApiSettings  # noqa: E402
from lyme_gap_atlas_api.snowflake_pool_metrics import READ_METRICS, WRITE_METRICS  # noqa: E402

MODES = ("no_pool", "pooled")
ALLOW_ENV = "ATLAS_BENCHMARK_ALLOW_STAGING"
CONFIRM_ENV = "ATLAS_BENCHMARK_CONFIRM_DATABASE"
_FORBIDDEN = re.compile(r"prod|accountadmin|securityadmin|sysadmin", re.IGNORECASE)


@dataclass(frozen=True)
class Scenario:
    """One request shape: ``borrows`` sequential pool borrows, ``nested`` re-entrant ones."""

    name: str
    pool: str  # "read" | "write"
    borrows: int = 1
    nested: int = 0  # extra re-entrant read borrows inside the first (same thread)
    work_factor: float = 1.0  # multiplies --work-ms (Ask Atlas holds its session longer)


SCENARIOS = (
    Scenario("public_read_single", "read"),
    Scenario("public_read_detail_nested", "read", borrows=1, nested=1),
    Scenario("public_read_multi_borrow", "read", borrows=3),
    Scenario("feedback_submit", "write"),
    Scenario("ask_atlas_chat_session", "write", work_factor=4.0),
)


class FakeConnection:
    def __init__(self, work_seconds: float) -> None:
        self.closed = False
        self._work = work_seconds

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def work(self) -> None:
        time.sleep(self._work)


class Counters:
    def __init__(self) -> None:
        self.logins = 0
        self._lock = threading.Lock()

    def login(self, login_seconds: float, work_seconds: float) -> FakeConnection:
        time.sleep(login_seconds)
        with self._lock:
            self.logins += 1
        return FakeConnection(work_seconds)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


def check_staging_guard(env: Mapping[str, str], settings: ApiSettings) -> str | None:
    """Return a refusal message, or None when a staging run is explicitly authorised."""
    database = settings.snowflake_database or ""
    if _FORBIDDEN.search(database) or _FORBIDDEN.search(settings.snowflake_role or ""):
        return "refusing to run: the configured Snowflake database/role looks like production"
    if env.get(ALLOW_ENV) != "1":
        return f"refusing to run: set {ALLOW_ENV}=1 to opt in to a staging benchmark"
    if not database or env.get(CONFIRM_ENV) != database:
        return f"refusing to run: set {CONFIRM_ENV} to the exact configured database name"
    return None


def run_scenario(
    scenario: Scenario,
    mode: str,
    *,
    requests: int,
    concurrency: int,
    settings: ApiSettings,
    login_seconds: float,
    work_seconds: float,
    staging: bool = False,
) -> dict[str, Any]:
    counters = Counters()
    waits: list[float] = []
    totals: list[float] = []
    failures = 0
    lock = threading.Lock()
    work = work_seconds * scenario.work_factor
    read_pool = read_module.ReadSessionPool(reaper_interval_seconds=None)
    write_pool = write_module.WriteSessionPool()
    READ_METRICS.reset()
    WRITE_METRICS.reset()

    def fake_connect(_settings: Any, **_: Any) -> FakeConnection:
        return counters.login(login_seconds, work)

    def borrow(stack: ExitStack) -> Any:
        """Borrow once; returns the connection and records the checkout wait."""
        started = time.perf_counter()
        if mode == "no_pool":
            connection = _staging_connect(settings) if staging else fake_connect(settings)
            stack.callback(connection.close)
        elif scenario.pool == "read":
            connection = stack.enter_context(read_module.read_connection(settings))
        else:
            connection = stack.enter_context(
                write_pool.borrow(settings, _staging_connect if staging else fake_connect)
            )
        with lock:
            waits.append((time.perf_counter() - started) * 1000)
        return connection

    def use(connection: Any) -> None:
        if staging:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        else:
            connection.work()

    def one_request() -> None:
        nonlocal failures
        started = time.perf_counter()
        try:
            for _ in range(scenario.borrows):
                with ExitStack() as stack:
                    connection = borrow(stack)
                    use(connection)
                    for _ in range(scenario.nested):
                        with ExitStack() as inner:
                            use(borrow(inner))
        except Exception:
            with lock:
                failures += 1
        finally:
            with lock:
                totals.append((time.perf_counter() - started) * 1000)

    patches: list[Any] = []
    if not staging:
        patches = [
            patch.object(read_module, "connect", fake_connect),
            patch.object(read_module, "apply_baseline", lambda *_a, **_k: None),
        ]
    with ExitStack() as patched:
        for item in patches:
            patched.enter_context(item)
        patched.enter_context(patch.object(read_module, "_POOL", read_pool))
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            for future in [executor.submit(one_request) for _ in range(requests)]:
                future.result()
        elapsed = time.perf_counter() - started

    metrics = (READ_METRICS if scenario.pool == "read" else WRITE_METRICS).snapshot()
    total_borrows = requests * scenario.borrows * (1 + scenario.nested)
    logins = counters.logins
    return {
        "scenario": scenario.name,
        "pool": scenario.pool,
        "mode": mode,
        "requests": requests,
        "borrows": total_borrows,
        "logins": logins,
        "logins_per_call": round(logins / requests, 3),
        "checkout_wait_ms_p50": round(percentile(waits, 0.50), 2),
        "checkout_wait_ms_p95": round(percentile(waits, 0.95), 2),
        "request_ms_p50": round(percentile(totals, 0.50), 2),
        "request_ms_p95": round(percentile(totals, 0.95), 2),
        "pool_exhausted": metrics["exhausted"] if mode == "pooled" else 0,
        "failed_requests": failures,
        "throughput_rps": round(requests / elapsed, 1) if elapsed else 0.0,
    }


def _staging_connect(settings: Any, **_: Any) -> Any:
    from lyme_gap_atlas_api.dependency_telemetry import connect

    return connect(settings)


def run_benchmark(
    *,
    requests: int,
    concurrency: int,
    pool_size: int,
    login_ms: float,
    work_ms: float,
    staging: bool = False,
    settings: ApiSettings | None = None,
) -> list[dict[str, Any]]:
    base = settings or ApiSettings()
    config = base.model_copy(
        update={
            "read_pool_max_size": pool_size,
            "write_pool_max_size": min(pool_size, 16),
            "read_pool_checkout_timeout_seconds": 30,
            "write_pool_checkout_timeout_seconds": 30,
        }
    )
    results = []
    for scenario in SCENARIOS:
        for mode in MODES:
            results.append(
                run_scenario(
                    scenario,
                    mode,
                    requests=requests,
                    concurrency=concurrency,
                    settings=config,
                    login_seconds=login_ms / 1000,
                    work_seconds=work_ms / 1000,
                    staging=staging,
                )
            )
    return results


def markdown_table(results: list[dict[str, Any]]) -> str:
    header = (
        "| scenario | mode | requests | logins | logins/call | wait p50 ms | wait p95 ms "
        "| request p95 ms | exhausted |\n|---|---|---:|---:|---:|---:|---:|---:|---:|"
    )
    rows = [
        f"| {r['scenario']} | {r['mode']} | {r['requests']} | {r['logins']} | "
        f"{r['logins_per_call']} | {r['checkout_wait_ms_p50']} | {r['checkout_wait_ms_p95']} | "
        f"{r['request_ms_p95']} | {r['pool_exhausted']} |"
        for r in results
    ]
    return "\n".join([header, *rows])


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    settings: ApiSettings | None = None,
    out: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--pool-size", type=int, default=8)
    parser.add_argument("--login-ms", type=float, default=20.0, help="simulated login cost")
    parser.add_argument("--work-ms", type=float, default=3.0, help="simulated statement cost")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--staging", action="store_true", help="opt-in real staging run")
    args = parser.parse_args(argv)
    if not (1 <= args.requests <= 5000 and 1 <= args.concurrency <= 64):
        parser.error("requests must be 1..5000 and concurrency 1..64")
    if not 1 <= args.pool_size <= 64:
        parser.error("pool-size must be 1..64")
    if args.staging:
        configured = settings or ApiSettings()
        refusal = check_staging_guard(os.environ if env is None else env, configured)
        if refusal:
            out(refusal)
            return 2
        if args.concurrency > 8 or args.requests > 200:
            out("refusing to run: staging runs are capped at 200 requests and concurrency 8")
            return 2
        settings = configured
    results = run_benchmark(
        requests=args.requests,
        concurrency=args.concurrency,
        pool_size=args.pool_size,
        login_ms=args.login_ms,
        work_ms=args.work_ms,
        staging=args.staging,
        settings=settings,
    )
    evidence = "staging, real connector" if args.staging else "simulated fakes; not production"
    if args.format == "markdown":
        out(f"Evidence: {evidence}\n\n{markdown_table(results)}")
    else:
        out(json.dumps({"evidence": evidence, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
