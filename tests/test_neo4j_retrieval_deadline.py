"""Real pinned-driver cancellation against a controlled local Bolt peer, no credentials."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import anyio
import pytest
from neo4j import GraphDatabase, Query
from neo4j._async.io._bolt import AsyncBolt
from neo4j._codec.packstream.v1 import PackableBuffer, Packer
from neo4j.exceptions import DatabaseUnavailable, ServiceUnavailable

import lyme_gap_atlas_api.knowledge_chat as chat
from lyme_gap_atlas_api.models import KnowledgeChatRequest

FIELDS = ["passage_id", "excerpt", "summary", "pmid", "title", "pubmed_url"]
ROW = [
    "fixture-passage",
    "Governed fixture.",
    "Fixture",
    "1234",
    "Fixture paper",
    "https://pubmed.ncbi.nlm.nih.gov/1234/",
]
BUDGET = 0.1
# A broad scheduling allowance, still three orders below the reported 120s stall.
MAX_ELAPSED = 1.0


class BoltPeer:
    """Minimal Bolt 4.4 server that can withhold replies for >120 seconds."""

    def __init__(self, stall: str = "", transient: bool = False, delay: float = 0) -> None:
        self.stall = stall
        self.transient = transient
        self.delay = delay
        self.tasks: set[asyncio.Task[Any]] = set()
        self.accepted = 0
        self.disconnected = 0
        self.begin_count = 0
        self.reached: set[str] = set()
        self.pull_ready = asyncio.Event()

    async def reply(self, writer: asyncio.StreamWriter, tag: bytes, fields: list[Any]) -> None:
        buffer = PackableBuffer()
        Packer(buffer).pack_struct(tag, fields)
        data = bytes(buffer.data)
        writer.write(len(data).to_bytes(2, "big") + data + b"\x00\x00")
        await writer.drain()

    async def pause(self, reader: asyncio.StreamReader, phase: str) -> bool:
        self.reached.add(phase)
        if phase == "pull":
            self.pull_ready.set()
        if phase == "pull" and self.delay:
            await asyncio.sleep(self.delay)
        if self.stall != phase:
            return False
        # Never reply. Unbounded old sync code waits until peer/server timeout;
        # new code must close the socket so this finishes on EOF, not on 125s.
        async with asyncio.timeout(125):
            while await reader.read(65536):
                pass
        return True

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        self.accepted += 1
        try:
            await reader.readexactly(20)
            if await self.pause(reader, "handshake"):
                return
            writer.write(b"\x00\x00\x04\x04")
            await writer.drain()
            while True:
                chunks = bytearray()
                while True:
                    size = int.from_bytes(await reader.readexactly(2), "big")
                    if not size:
                        break
                    chunks.extend(await reader.readexactly(size))
                tag = chunks[1]
                if tag == 0x01:  # HELLO
                    await self.reply(writer, b"\x70", [{"server": "Neo4j/5.26.0"}])
                elif tag == 0x11:  # BEGIN
                    self.begin_count += 1
                    if await self.pause(reader, "begin"):
                        return
                    if self.transient:
                        await self.reply(
                            writer,
                            b"\x7f",
                            [
                                {
                                    "code": "Neo.TransientError.General.DatabaseUnavailable",
                                    "message": "controlled fixture",
                                }
                            ],
                        )
                    else:
                        await self.reply(writer, b"\x70", [{}])
                elif tag == 0x10:  # RUN
                    if await self.pause(reader, "run"):
                        return
                    await self.reply(writer, b"\x70", [{"fields": FIELDS}])
                elif tag == 0x3F:  # PULL (eager result consumption)
                    if await self.pause(reader, "pull"):
                        return
                    await self.reply(writer, b"\x71", [ROW])
                    await self.reply(writer, b"\x70", [{"type": "r", "db": "neo4j"}])
                elif tag == 0x12:  # COMMIT
                    if await self.pause(reader, "commit"):
                        return
                    await self.reply(writer, b"\x70", [{}])
                elif tag == 0x0F:  # RESET
                    if await self.pause(reader, "reset"):
                        return
                    await self.reply(writer, b"\x70", [{}])
                elif tag == 0x02:  # GOODBYE
                    return
                else:
                    raise AssertionError(f"Unexpected Bolt signature {tag}")
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            self.disconnected += 1
            self.tasks.remove(task)


@asynccontextmanager
async def peer_uri(peer: BoltPeer) -> AsyncIterator[str]:
    server = await asyncio.start_server(peer.handle, "127.0.0.1", 0)
    try:
        yield f"bolt://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        # Test the actual driver socket was closed, not just task.cancel() called.
        async with asyncio.timeout(MAX_ELAPSED):
            while peer.tasks:
                await asyncio.sleep(0.001)
        assert peer.accepted == peer.disconnected
    finally:
        server.close()
        await server.wait_closed()
        tasks = list(peer.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def retriever(uri: str) -> chat.Neo4jRetriever:
    result = chat.Neo4jRetriever.__new__(chat.Neo4jRetriever)
    result._uri = uri
    result._auth = ("fixture", "fixture")
    return result


@pytest.mark.parametrize("phase", ["handshake", "begin", "run", "pull", "commit"])
def test_real_driver_bounds_125_second_stall_and_closes_socket(phase: str) -> None:
    async def run() -> None:
        peer = BoltPeer(stall=phase)
        async with peer_uri(peer) as uri:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await retriever(uri)._retrieve("fixture", [0.1], started + BUDGET)
            elapsed = time.monotonic() - started
            assert BUDGET * 0.8 <= elapsed < MAX_ELAPSED
            assert phase in peer.reached
            print(f"{phase}: {elapsed * 1000:.1f} ms, budget {BUDGET * 1000:.0f} ms")

    asyncio.run(run())


def test_real_driver_normal_result_and_cleanup() -> None:
    async def run() -> None:
        peer = BoltPeer()
        async with peer_uri(peer) as uri:
            records = await retriever(uri)._retrieve("fixture", [0.1], time.monotonic() + 2)
            assert dict(records[0]) == dict(zip(FIELDS, ROW, strict=True))
            assert peer.begin_count == 1

    asyncio.run(run())


def test_real_driver_transient_failure_has_no_managed_retry() -> None:
    async def run() -> None:
        peer = BoltPeer(transient=True)
        async with peer_uri(peer) as uri:
            started = time.monotonic()
            with pytest.raises(DatabaseUnavailable):
                await retriever(uri)._retrieve("fixture", [0.1], started + 2)
            assert time.monotonic() - started < MAX_ELAPSED
            assert peer.begin_count == 1

    asyncio.run(run())


def test_transient_failure_reset_stall_remains_within_original_deadline() -> None:
    async def run() -> None:
        peer = BoltPeer(stall="reset", transient=True)
        async with peer_uri(peer) as uri:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await retriever(uri)._retrieve("fixture", [0.1], started + BUDGET)
            assert time.monotonic() - started < MAX_ELAPSED
            assert peer.begin_count == 1
            assert "reset" in peer.reached

    asyncio.run(run())


def test_real_driver_failed_connection_is_bounded() -> None:
    async def run() -> None:
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        started = time.monotonic()
        with pytest.raises((ServiceUnavailable, TimeoutError)):
            await retriever(f"bolt://127.0.0.1:{port}")._retrieve(
                "fixture", [0.1], started + BUDGET
            )
        assert time.monotonic() - started < MAX_ELAPSED

    asyncio.run(run())


def test_real_driver_cleanup_stall_is_cancelled_and_connection_killed(monkeypatch: Any) -> None:
    original = AsyncBolt.close
    entered = False

    async def slow_close(connection: Any) -> None:
        nonlocal entered
        entered = True
        await asyncio.sleep(125)
        await original(connection)

    monkeypatch.setattr(AsyncBolt, "close", slow_close)

    async def run() -> None:
        peer = BoltPeer()
        async with peer_uri(peer) as uri:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await retriever(uri)._retrieve("fixture", [0.1], started + BUDGET)
            assert entered and time.monotonic() - started < MAX_ELAPSED

    asyncio.run(run())


class Embeddings:
    def with_options(self, **kwargs: Any) -> Embeddings:
        return self

    @property
    def embeddings(self) -> Embeddings:
        return self

    def create(self, **kwargs: Any) -> Any:
        return type("Result", (), {"data": [type("Vector", (), {"embedding": [0.1]})()]})()


@pytest.mark.parametrize("client_setup_delay", [0.0, MAX_ELAPSED + 0.05])
def test_http_timeout_is_typed_classified_and_has_no_generation(
    monkeypatch: Any, caplog: Any, client_setup_delay: float
) -> None:
    from httpx import ASGITransport, AsyncClient

    from lyme_gap_atlas_api.app import create_app
    from lyme_gap_atlas_api.config import ApiSettings

    monkeypatch.setattr("lyme_gap_atlas_api.app.configure_logging", lambda: None)
    monkeypatch.setattr(chat, "_NEO4J_RETRIEVAL_SECONDS", BUDGET)
    logging.getLogger().addHandler(caplog.handler)

    async def run() -> None:
        peer = BoltPeer()
        async with peer_uri(peer) as uri:
            r = retriever(uri)
            r._openai = Embeddings()  # type: ignore[assignment]
            # Complete a real pinned-driver exchange before starting the HTTP
            # cancellation clock. This warms imports/protocol setup and signals
            # that the fixture can reach the intended PULL phase.
            async with asyncio.timeout(10):
                await r._retrieve("fixture", [0.1], time.monotonic() + 10)
                await peer.pull_ready.wait()
            peer.stall = "pull"
            peer.begin_count = 0
            peer.reached.clear()
            service = chat.KnowledgeChatService(r, None, None, "fixture")  # type: ignore[arg-type]
            app = create_app(
                settings=ApiSettings(knowledge_chat_enabled=True), knowledge_chat_service=service
            )
            # Run the in-process HTTP client on the Bolt peer's event loop.
            # TestClient plus to_thread added a second portal/worker hop before
            # service entry, so CI thread scheduling consumed the cancellation
            # assertion's wall clock. Prepare the ordinary ASGI sync worker first.
            await anyio.to_thread.run_sync(lambda: None)
            with caplog.at_level(logging.INFO):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://fixture"
                ) as client:
                    # Deliberately slow client preparation must not masquerade as
                    # slow backend cancellation. The request bound stays 1s.
                    await asyncio.sleep(client_setup_delay)
                    started = time.monotonic()
                    async with asyncio.timeout(MAX_ELAPSED):
                        response = await client.post(
                            "/v1/knowledge-graph/chat", json={"message": "fixture"}
                        )
                    assert time.monotonic() - started < MAX_ELAPSED
            assert response.status_code == 200
            assert response.json()["status"] == "evidence_unavailable"
            assert response.json()["claims"] == []
            # The service deadline also covers connection setup: cancellation
            # may legitimately precede BEGIN. Retrying a transaction is forbidden.
            assert peer.begin_count <= 1
        assert any(
            r.msg == "knowledge_chat_total"
            and r.context["outcome"] == "neo4j_timeout"
            and r.context["generation_attempts"] == 0
            and r.context["service_duration_ms"] < MAX_ELAPSED * 1000
            and BUDGET * 800
            <= r.context["stage_latencies_ms"]["neo4j_retrieval"]
            < MAX_ELAPSED * 1000
            for r in caplog.records
        )

    asyncio.run(run())


def test_repeated_timeouts_leave_no_driver_tasks_or_threads() -> None:
    threads = set(threading.enumerate())

    async def run() -> None:
        baseline = asyncio.all_tasks()
        peer = BoltPeer(stall="pull")
        async with peer_uri(peer) as uri:
            for _ in range(4):
                with pytest.raises(TimeoutError):
                    await retriever(uri)._retrieve("fixture", [0.1], time.monotonic() + BUDGET)
            async with asyncio.timeout(MAX_ELAPSED):
                while peer.tasks:
                    await asyncio.sleep(0.001)
            assert peer.begin_count == 4
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == baseline

    asyncio.run(run())
    assert set(threading.enumerate()) <= threads


def test_service_remaining_budget_is_propagated_and_reset() -> None:
    observed: list[float | None] = []

    class BudgetRetriever:
        def search(self, message: str, request_id: str) -> list[chat.Evidence]:
            observed.append(chat._RETRIEVAL_DEADLINE.get())
            return []

    service = chat.KnowledgeChatService(BudgetRetriever(), None, None, "fixture")  # type: ignore[arg-type]
    before = time.monotonic()
    service.chat(KnowledgeChatRequest(message="fixture"), "fixture", "fixture")
    after = time.monotonic()
    assert observed[0] is not None and before + 28 <= observed[0] <= after + 28
    assert chat._RETRIEVAL_DEADLINE.get() is None


def test_expired_deadline_does_not_start_driver(monkeypatch: Any) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("No connection should be attempted")

    monkeypatch.setattr(chat.AsyncGraphDatabase, "driver", forbidden)
    with pytest.raises(TimeoutError):
        asyncio.run(retriever("bolt://unused")._retrieve("fixture", [0.1], time.monotonic() - 1))


def test_six_concurrent_stalls_release_all_sessions() -> None:
    async def run() -> None:
        peer = BoltPeer()
        baseline = asyncio.all_tasks()
        async with peer_uri(peer) as uri:
            # Verify the real fixture and driver before measuring six concurrent
            # cancellations. The measured calls still create their own drivers.
            async with asyncio.timeout(10):
                await retriever(uri)._retrieve("fixture", [0.1], time.monotonic() + 10)
                await peer.pull_ready.wait()
            accepted_before = peer.accepted
            peer.stall = "pull"
            ready = asyncio.Barrier(7)
            durations: list[float] = []

            async def request() -> None:
                await ready.wait()
                request_started = time.monotonic()
                with pytest.raises(TimeoutError):
                    await retriever(uri)._retrieve("fixture", [0.1], request_started + BUDGET)
                durations.append(time.monotonic() - request_started)

            requests = [asyncio.create_task(request()) for _ in range(6)]
            started = time.monotonic()
            async with asyncio.timeout(MAX_ELAPSED):
                await ready.wait()
                await asyncio.gather(*requests)
            elapsed = time.monotonic() - started
            assert elapsed < MAX_ELAPSED, (elapsed, durations)
            assert len(durations) == 6 and all(BUDGET * 0.8 <= d < MAX_ELAPSED for d in durations)
            assert peer.accepted - accepted_before == 6
        await asyncio.sleep(0)
        assert asyncio.all_tasks() == baseline

    asyncio.run(run())


def test_before_after_same_slow_peer() -> None:
    async def run() -> None:
        peer = BoltPeer(delay=0.35)
        async with peer_uri(peer) as uri:

            def old_retrieval() -> None:
                with GraphDatabase.driver(uri, auth=("fixture", "fixture")) as driver:
                    driver.execute_query(
                        Query(chat.HYBRID_SEARCH, timeout=5),
                        {"query": "fixture", "embedding": [0.1]},
                        database_="neo4j",
                    )

            started = time.monotonic()
            await anyio.to_thread.run_sync(old_retrieval)
            before = time.monotonic() - started
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await retriever(uri)._retrieve("fixture", [0.1], started + BUDGET)
            after = time.monotonic() - started
            assert before >= 0.35 and after < MAX_ELAPSED
            assert after < before
            print(f"same peer: before={before * 1000:.1f} ms after={after * 1000:.1f} ms")

    asyncio.run(run())


def test_worker_bridge_preserves_request_deadline(monkeypatch: Any) -> None:
    observed = []

    async def retrieve(
        self: Any, message: str, embedding: list[float], deadline: float
    ) -> list[Any]:
        observed.append(deadline)
        return []

    monkeypatch.setattr(chat.Neo4jRetriever, "_retrieve", retrieve)
    r = retriever("bolt://unused")
    r._openai = Embeddings()  # type: ignore[assignment]

    async def run() -> None:
        deadline = time.monotonic() + 0.5
        token = chat._RETRIEVAL_DEADLINE.set(deadline)
        try:
            await anyio.to_thread.run_sync(r.search, "fixture", "fixture")
        finally:
            chat._RETRIEVAL_DEADLINE.reset(token)
        assert observed == [deadline]

    anyio.run(run)
