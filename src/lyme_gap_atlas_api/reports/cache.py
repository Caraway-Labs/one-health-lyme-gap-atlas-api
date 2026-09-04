"""Bounded, in-process cache for completed PDF report artifacts."""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Entry:
    payload: bytes
    expires_at: float


class PdfReportCache:
    """Caches rendered bytes and serializes only identical cache misses."""

    def __init__(self, *, enabled: bool, ttl_seconds: int, max_entries: int) -> None:
        self._enabled = enabled
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def get_or_render(self, key: str, render: Callable[[], bytes]) -> bytes:
        if not self._enabled:
            logger.info("pdf_cache_miss", extra={"cache_key": key, "cache_enabled": False})
            return self._render(key, render)

        with self._lock:
            cached = self._get_locked(key)
            if cached is not None:
                logger.info("pdf_cache_hit", extra={"cache_key": key})
                return cached
            key_lock = self._locks.setdefault(key, threading.Lock())

        with key_lock:
            with self._lock:
                cached = self._get_locked(key)
                if cached is not None:
                    logger.info("pdf_cache_hit", extra={"cache_key": key})
                    return cached
            payload = self._render(key, render)
            with self._lock:
                self._entries[key] = _Entry(payload, time.monotonic() + self._ttl_seconds)
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
                self._locks.pop(key, None)
            return payload

    def _get_locked(self, key: str) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._entries.pop(key)
            return None
        self._entries.move_to_end(key)
        return entry.payload

    @staticmethod
    def _render(key: str, render: Callable[[], bytes]) -> bytes:
        logger.info("pdf_cache_render", extra={"cache_key": key})
        return render()
