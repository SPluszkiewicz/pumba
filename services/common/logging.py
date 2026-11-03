"""Structured JSON logging shared by every Python service, on `structlog`.

Before this module each service re-implemented the same three lines
(`print(json.dumps({"event": ..., **fields}))`). `structlog` gives the same
one-JSON-object-per-line output on stdout - so `podman compose logs` and any
log shipper keep working - plus what the hand-rolled version lacked: a
`level`, an ISO `timestamp`, and **context binding**: the `agent` binds
`request_id` once per request (`bind_request_id`) and every log line emitted
while serving it carries it, including lines from modules that never see the
request object.

Usage::

    from common.logging import get_logger          # flat-layout services
    from services.common.logging import get_logger  # package-layout services

    log = get_logger("rag")
    log.info("qdrant_search_degraded", reason="collection_missing")

The `event` key stays the first-class identifier (same key as before), and
extra keyword arguments become JSON fields.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

__all__ = ["configure", "get_logger", "bind_request_id", "clear_request_context"]

_configured = False


def configure(*, level: str | None = None, stream: Any = None) -> None:
    """Configure structlog once per process. Idempotent; safe to call from tests.

    `LOG_LEVEL` (env) or `level` selects the minimum level (default INFO).
    `stream` overrides stdout (tests pass an `io.StringIO`).
    """
    global _configured
    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    min_level = logging.getLevelName(level_name)
    if not isinstance(min_level, int):
        min_level = logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(sort_keys=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stdout),
        # Tests reconfigure with a different stream; caching would pin the first one.
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger(component: str) -> Any:
    """A bound logger tagged with `component` (the module/service name)."""
    if not _configured:
        configure()
    return structlog.get_logger().bind(component=component)


def bind_request_id(request_id: str) -> None:
    """Attach `request_id` to every log line emitted in this async context."""
    structlog.contextvars.bind_contextvars(request_id=request_id)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
