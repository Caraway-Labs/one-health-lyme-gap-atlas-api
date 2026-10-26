"""Shared assertions for rows the intelligence lists omit instead of serving."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

ROW_SKIPPED = "intelligence_feed_row_skipped"
ROWS_SKIPPED = "intelligence_feed_rows_skipped"
HEALTHY_ITEM_ID = "c" * 64
_ROW_CONTEXT = {"route", "item_id", "source_id", "fields", "error_class"}


def healthy_copy(document: dict[str, Any]) -> dict[str, Any]:
    """A valid sibling row with its own identity, taken before the row is made unsafe."""
    healthy = deepcopy(document)
    healthy["item_id"] = HEALTHY_ITEM_ID
    if "deduplication_key" in healthy:
        healthy["deduplication_key"] = HEALTHY_ITEM_ID
    return healthy


class SkipLog(logging.Handler):
    """Every API log record; the app's logging setup replaces root (caplog) handlers."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def clear(self) -> None:
        self.records.clear()

    @property
    def text(self) -> str:
        return "\n".join(
            record.getMessage() + json.dumps(getattr(record, "context", None), default=str)
            for record in self.records
        )


@contextmanager
def skip_log() -> Iterator[SkipLog]:
    logger = logging.getLogger("lyme_gap_atlas_api")
    handler = SkipLog()
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _records(log: SkipLog, message: str, route: str) -> list[Any]:
    return [
        record
        for record in log.records
        if record.getMessage() == message and getattr(record, "context", {}).get("route") == route
    ]


def assert_row_skipped(
    log: SkipLog,
    *,
    route: str,
    item_id: str,
    source_id: str,
    field: str,
    forbidden: tuple[str, ...],
    skipped: int = 1,
) -> None:
    """One WARNING per skipped row with identifiers and field paths only."""
    rows = _records(log, ROW_SKIPPED, route)
    assert len(rows) == skipped
    for record in rows:
        assert record.levelname == "WARNING"
        assert set(record.context) == _ROW_CONTEXT
        assert record.context["item_id"] == item_id
        assert record.context["source_id"] == source_id
        assert record.context["fields"]
        assert any(
            name == field or name.startswith((f"{field}.", f"{field}["))
            for name in record.context["fields"]
        ), record.context["fields"]
    summaries = _records(log, ROWS_SKIPPED, route)
    assert [record.context["skipped_count"] for record in summaries] == [skipped]
    for value in forbidden:
        assert value not in log.text
