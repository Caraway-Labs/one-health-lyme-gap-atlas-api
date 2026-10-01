"""Offline scheduling proof: slow synchronous chat never occupies the event loop."""

import asyncio
from threading import Event, get_ident
from typing import Any, cast

import httpx

from lyme_gap_atlas_api.app import create_app
from lyme_gap_atlas_api.config import ApiSettings
from lyme_gap_atlas_api.knowledge_chat import KnowledgeChatService
from lyme_gap_atlas_api.models import KnowledgeChatResponse
from lyme_gap_atlas_api.repository import AtlasRepository


class SlowService:
    def __init__(self) -> None:
        self.release = Event()
        self.started = 0
        self.finished = 0
        self.worker_ids: list[int] = []

    def chat(self, payload: Any, request_id: str, network: str, completion: Any) -> Any:
        self.started += 1
        self.worker_ids.append(get_ident())
        try:
            assert self.release.wait(timeout=3), "test worker release was not signalled"
            return KnowledgeChatResponse(
                request_id=request_id,
                conversation_id="test",
                configuration_version="kg-v1.0.0",
                assistant_policy_version="assistant-policy-v1",
                source_used="literature_evidence",
                status="no_evidence",
                answer="No evidence",
                evidence_state="no_relevant_corpus_evidence",
            )
        finally:
            self.finished += 1


async def wait_for_started(service: SlowService, count: int) -> None:
    async def wait() -> None:
        while service.started < count:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=2)


def test_slow_chat_offloads_io_and_preserves_concurrency_limit() -> None:
    async def run() -> None:
        service = SlowService()
        app = create_app(
            cast(AtlasRepository, object()),
            ApiSettings(knowledge_chat_enabled=True),
            cast(KnowledgeChatService, service),
        )
        event_loop_thread = get_ident()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            requests = [
                asyncio.create_task(
                    client.post(
                        "/v1/knowledge-graph/chat",
                        json={"message": "fixture"},
                    )
                )
                for _ in range(3)
            ]
            try:
                await wait_for_started(service, 3)
                # These complete while all three synchronous workers remain blocked.
                health = await asyncio.wait_for(client.get("/health/live"), timeout=1)
                rejected = await asyncio.wait_for(
                    client.post(
                        "/v1/knowledge-graph/chat",
                        json={"message": "fixture"},
                    ),
                    timeout=1,
                )
                assert health.status_code == 200
                assert rejected.status_code == 429
                assert service.started == 3 and service.finished == 0
                assert all(worker != event_loop_thread for worker in service.worker_ids)
            finally:
                service.release.set()
                responses = await asyncio.gather(*requests)
            assert all(response.status_code == 200 for response in responses)
            assert service.finished == 3
            # Limiter slots are returned when workers complete.
            after = await client.post("/v1/knowledge-graph/chat", json={"message": "fixture"})
            assert after.status_code == 200

    asyncio.run(run())
