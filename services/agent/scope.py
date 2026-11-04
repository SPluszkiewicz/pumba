"""Per-request checkout. Task-local, so two questions do not share a mount."""

from __future__ import annotations

import os
from contextvars import ContextVar

from common.qdrant_schema import DEFAULT_COLLECTION

project_root_header: ContextVar[str] = ContextVar("project_root_header", default="")
qdrant_collection: ContextVar[str] = ContextVar("qdrant_collection", default="")
active_checkout_id: ContextVar[str] = ContextVar("active_checkout_id", default="")


def current_collection() -> str:
    override = qdrant_collection.get().strip()
    if override:
        return override
    return os.getenv("QDRANT_COLLECTION", DEFAULT_COLLECTION)


def current_checkout_id() -> str:
    return active_checkout_id.get().strip()
