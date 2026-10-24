"""Test helper: stand in for ``read_connection`` with a fake connection factory."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any


def lend(factory: Callable[[Any], Any]) -> Callable[[Any], Any]:
    @contextmanager
    def read_connection(settings: Any) -> Iterator[Any]:
        yield factory(settings)

    return read_connection
