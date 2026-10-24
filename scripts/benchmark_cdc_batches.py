"""Synthetic integrated CDC path comparison; no credentials or production reads.

uv run python scripts/benchmark_cdc_batches.py --samples 2
The real service/adapter map controlled fixture envelopes. The producer doubles
exercise the borrowed query callback but do not prove scientific admission.
"""

import argparse
import csv
import json
import sys
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_cdc_sessions import baseline_module  # noqa: E402
from lyme_gap_atlas_data import cdc_consumer_batch as batch  # noqa: E402
from lyme_gap_atlas_data import cdc_consumer_runtime as runtime  # noqa: E402
from test_cdc_request_session import Cursor, Session  # noqa: E402
from test_cdc_surveillance import envelope, params, settings  # noqa: E402

from lyme_gap_atlas_api.cdc_surveillance import CDCObservationAdapter  # noqa: E402
from lyme_gap_atlas_api.cdc_timing import CDCTiming, cdc_request_timing  # noqa: E402
from lyme_gap_atlas_api.public_contract import ObservationQuery  # noqa: E402
from lyme_gap_atlas_api.public_observations import (  # noqa: E402
    ObservationService,
    SnowflakeObservationRepository,
)

BASELINE = "6d7908bcfbfb4d90cddcfaefe03d1836f08490ba"
MARKER = "synthetic-batch-authority"


def fixture(measure, county):
    value = envelope(measure, county)
    value["semantic"]["observation"]["id"] += f":{county}"
    value["semantic"]["lineage"]["semantic_observation_id"] = value["semantic"]["observation"]["id"]
    value["surveillance_evidence"]["observation_key"] = value["semantic"]["observation"]["id"]
    return value


def singleton(query, root, measure, county, environment, *, release_id):
    assert environment == "prod"
    assert query(runtime.IDENTITY_SQL, ())[0]["ROLE"] == "OH_LYME_PROD_READ"
    query(runtime.AUTHORITY_SQL, (release_id, measure, county, "synthetic-source"))
    return fixture(measure, county)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".agent-tools/api248-stage2/benchmark.csv"
    )
    args = parser.parse_args()
    if not 1 <= args.samples <= 10:
        parser.error("samples must be 1..10")
    old_obs = baseline_module("public_observations", BASELINE)
    old_cdc = baseline_module("cdc_surveillance", BASELINE)
    config = settings(cdc_surveillance_enabled=True, public_query_timeout_seconds=5)
    durations: dict[str, float] = defaultdict(float)
    sessions: list[Session] = []
    records = []

    def delayed(name, seconds, call):
        started = time.perf_counter()
        try:
            time.sleep(seconds)
            return call()
        finally:
            durations[name] += (time.perf_counter() - started) * 1000

    class DelayCursor(Cursor):
        def execute(self, sql, params=(), *, timeout):
            def execute():
                super(DelayCursor, self).execute(sql, params, timeout=timeout)
                if sql == MARKER:
                    self.description = [("synthetic",)]
                    self.rows = [("batch",)]

            return delayed("execute_ms", 0.01, execute)

        def fetchone(self):
            return delayed("fetch_ms", 0.002, super().fetchone)

        def fetchmany(self, limit):
            return delayed("fetch_ms", 0.002, lambda: super(DelayCursor, self).fetchmany(limit))

    class DelaySession(Session):
        def cursor(self):
            return DelayCursor(self)

        def __exit__(self, *args):
            return delayed("teardown_ms", 0.01, lambda: super(DelaySession, self).__exit__(*args))

    def connect(*args, **kwargs):
        def create():
            session = DelaySession()
            sessions.append(session)
            return session

        return delayed("connect_ms", 0.1, create)

    def produce(query, measure, counties, *, environment, release_id):
        assert environment == "prod" and 1 <= len(counties) <= 50
        assert query(runtime.IDENTITY_SQL, ())[0]["ROLE"] == "OH_LYME_PROD_READ"
        assert query(MARKER, tuple(counties)) == [{"synthetic": "batch"}]
        return batch.CDCBatchResultV1(
            measure, release_id, tuple(counties), tuple(fixture(measure, c) for c in counties)
        )

    with (
        patch("lyme_gap_atlas_api.dependency_telemetry.snowflake_connect", connect),
        patch.object(runtime, "_project", singleton),
        patch.object(batch, "produce_cdc_batch_v1", produce),
    ):
        for measure in ["scapularis_status", "burgdorferi_status"]:
            for n in [1, 2, 5, 10, 50, 51]:
                counties = [f"{1001 + 2 * index:05d}" for index in range(n)]
                query = ObservationQuery(**params(measure, counties, page_size=100))
                for sample in range(args.samples):
                    baseline_data = None
                    for mode in ["baseline", "candidate"]:
                        durations.clear()
                        sessions.clear()
                        timing = CDCTiming()
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
                        tracemalloc.start()
                        started = time.perf_counter()
                        with cdc_request_timing(timing):
                            response = svc.search(query)
                        elapsed = (time.perf_counter() - started) * 1000
                        serialization_start = time.perf_counter()
                        public_payload = response.model_dump_json().encode("utf-8")
                        serialization_ms = (time.perf_counter() - serialization_start) * 1000
                        _, peak_bytes = tracemalloc.get_traced_memory()
                        tracemalloc.stop()
                        data = [item.model_dump(mode="json") for item in response.data]
                        if mode == "baseline":
                            baseline_data = data
                        else:
                            assert data == baseline_data
                        calls = [call for session in sessions for call in session.calls]
                        authority = sum(
                            sql in (runtime.AUTHORITY_SQL, MARKER) for sql, _, _ in calls
                        )
                        is_batch = mode == "candidate" and n <= 50
                        assert len(sessions) == 1
                        assert len(calls) == (5 if is_batch else 3 + 2 * n)
                        assert authority == (1 if is_batch else n)
                        assert all(session.closes == 1 for session in sessions)
                        records.append(
                            dict(
                                evidence="synthetic SDK and producer; local wall clock",
                                mode=mode,
                                measure=measure,
                                processed_counties=n,
                                sample=sample + 1,
                                connections=len(sessions),
                                authority_queries=authority,
                                explicit_sql=len(calls),
                                simulated_total_ms=elapsed,
                                mapping_ms=timing.durations.get("mapping", 0),
                                serialization_ms=serialization_ms,
                                producer_processing_ms=timing.durations.get(
                                    "producer_processing", 0
                                ),
                                public_payload_bytes=len(public_payload),
                                projected_batch_bytes=timing.batch_envelope_bytes,
                                peak_python_bytes=peak_bytes,
                                **dict(durations),
                            )
                        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(
        json.dumps(
            dict(
                output=str(args.output),
                samples=len(records),
                baseline_ref=BASELINE,
                injected_cost_ms=dict(connect=100, execute=10, fetch=2, teardown=10),
                result="public DTO parity; connection cleanup and SQL counts asserted",
            )
        )
    )


if __name__ == "__main__":
    main()
