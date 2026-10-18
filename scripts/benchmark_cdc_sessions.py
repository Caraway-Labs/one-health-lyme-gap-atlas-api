"""Credential-free simulated CDC lifecycle comparison, never production evidence.

Run from the repository: uv run python scripts/benchmark_cdc_sessions.py
Loads the exact accepted baseline's API modules from Git, uses controlled SDK
and producer callbacks from tests, and asserts identical scientific DTOs.
"""

import argparse
import csv
import json
import subprocess
import sys
import time
import types
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from lyme_gap_atlas_data import cdc_consumer_runtime as runtime  # noqa: E402
from test_cdc_request_session import Cursor, Session, controlled_project  # noqa: E402
from test_cdc_surveillance import params, settings  # noqa: E402

from lyme_gap_atlas_api.cdc_surveillance import CDCObservationAdapter  # noqa: E402
from lyme_gap_atlas_api.public_contract import ObservationQuery  # noqa: E402
from lyme_gap_atlas_api.public_observations import (  # noqa: E402
    ObservationService,
    SnowflakeObservationRepository,
)

BASELINE = "ca65b7b689b6b417b122f25ff3597ddbdf36f98b"


def baseline_module(name: str, ref: str) -> types.ModuleType:
    source = subprocess.run(
        ["git", "show", f"{ref}:src/lyme_gap_atlas_api/{name}.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    module = types.ModuleType(f"lyme_gap_atlas_api._benchmark_baseline_{name}")
    module.__package__ = "lyme_gap_atlas_api"
    exec(compile(source, f"baseline/{name}.py", "exec"), module.__dict__)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-ref", default=BASELINE)
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".agent-tools/api248-stage1/benchmark.csv"
    )
    args = parser.parse_args()
    if not 1 <= args.samples <= 10:
        parser.error("samples must be 1..10")
    old_obs = baseline_module("public_observations", args.baseline_ref)
    old_cdc = baseline_module("cdc_surveillance", args.baseline_ref)
    config = settings(cdc_surveillance_enabled=True, public_query_timeout_seconds=5)
    records = []
    durations: dict[str, float] = defaultdict(float)
    sessions = []

    def delayed(name, seconds, call):
        start = time.perf_counter()
        try:
            time.sleep(seconds)
            return call()
        finally:
            durations[name] += (time.perf_counter() - start) * 1000

    class DelayCursor(Cursor):
        def execute(self, *a, **kw):
            return delayed("execute_ms", 0.01, lambda: super(DelayCursor, self).execute(*a, **kw))

        def fetchone(self):
            return delayed("fetch_ms", 0.002, super().fetchone)

        def fetchmany(self, limit):
            return delayed("fetch_ms", 0.002, lambda: super(DelayCursor, self).fetchmany(limit))

    class DelaySession(Session):
        def cursor(self):
            return DelayCursor(self)

        def __exit__(self, *a):
            return delayed("teardown_ms", 0.01, lambda: super(DelaySession, self).__exit__(*a))

    def connect(*a, **kw):
        def create():
            session = DelaySession()
            sessions.append(session)
            return session

        return delayed("connect_ms", 0.1, create)

    def project(*a, **kw):
        start = time.perf_counter()
        io_before = durations["execute_ms"] + durations["fetch_ms"]
        try:
            return controlled_project(*a, **kw)
        finally:
            duration = (time.perf_counter() - start) * 1000
            durations["producer_inclusive_ms"] += duration
            durations["producer_processing_ms"] += max(
                0, duration - (durations["execute_ms"] + durations["fetch_ms"] - io_before)
            )

    # Delay the actual common SDK constructor, preserving dependency instrumentation.
    with (
        patch("lyme_gap_atlas_api.dependency_telemetry.shared_connect", connect),
        patch.object(runtime, "_project", project),
    ):
        for measure in ["scapularis_status", "burgdorferi_status"]:
            for n in [1, 2, 5]:
                query = ObservationQuery(
                    **params(measure, ["01001", "01003", "01005", "01007", "01009"][:n])
                )
                for sample in range(args.samples):
                    baseline_data = None
                    for mode in ["baseline", "candidate"]:
                        durations.clear()
                        sessions.clear()
                        svc = (
                            old_obs.ObservationService(
                                old_obs.SnowflakeObservationRepository(config),
                                cdc_adapter=old_cdc.CDCObservationAdapter(config),
                            )
                            if mode == "baseline"
                            else ObservationService(
                                SnowflakeObservationRepository(config),
                                cdc_adapter=CDCObservationAdapter(config),
                            )
                        )
                        started = time.perf_counter()
                        response = svc.search(query)
                        elapsed = (time.perf_counter() - started) * 1000
                        data = [x.model_dump(mode="json") for x in response.data]
                        if mode == "baseline":
                            baseline_data = data
                        else:
                            assert data == baseline_data
                        assert all(s.closes == 1 for s in sessions)
                        sql = sum(len(s.calls) for s in sessions)
                        assert (len(sessions), sql) == (
                            (n + 1, 1 + 4 * n) if mode == "baseline" else (1, 3 + 2 * n)
                        )
                        records.append(
                            dict(
                                evidence="simulated SDK and producer; local wall clock",
                                mode=mode,
                                measure=measure,
                                counties=n,
                                sample=sample + 1,
                                connections=len(sessions),
                                explicit_sql_executions=sql,
                                total_ms=elapsed,
                                **dict(durations),
                            )
                        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(
        json.dumps(
            dict(
                output=str(args.output),
                samples=len(records),
                baseline_ref=args.baseline_ref,
                injected_cost_ms=dict(connect=100, execute=10, fetch=2, teardown=10),
                result="identical public data; all sessions closed; expected counters verified",
            )
        )
    )


if __name__ == "__main__":
    main()
