"""List chat GGUFs and hot-swap the one llama-server has loaded.

Router mode (`llama-server --models-dir`, no `-m`) exposes `GET /models` and
`POST /models/load|unload`. Only one model stays resident (`--models-max 1`).
Embedder and reranker files live in the same directory and are not offered.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import llm_client

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://llm:8080")
_LOAD_POLLS = 900
_POLL_SECONDS = 2.0


def _excluded_names() -> set[str]:
    names = set()
    for key in ("EMBEDDER_MODEL_PATH", "RERANKER_MODEL_PATH"):
        raw = os.getenv(key, "")
        if raw:
            names.add(Path(raw).name)
    return names


def _stem(name: str) -> str:
    base = Path(name).name
    return base[:-5] if base.lower().endswith(".gguf") else base


def is_chat_model(name: str, excluded: set[str] | None = None) -> bool:
    """True for a chat GGUF id. Router ids omit the `.gguf` suffix."""
    stem = _stem(name)
    lower = stem.lower()
    if not stem or lower in {"readme", "readme.md"}:
        return False
    blocked = {_stem(item) for item in (excluded if excluded is not None else _excluded_names())}
    if stem in blocked or Path(name).name in blocked:
        return False
    return not any(part in lower for part in ("embed", "rerank", "mmproj"))


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        models = payload.get("models")
        if isinstance(models, list):
            return [row for row in models if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def _status(row: dict[str, Any]) -> str:
    status = row.get("status")
    if isinstance(status, dict):
        return str(status.get("value") or "unloaded")
    if isinstance(status, str):
        return status
    return "unloaded"


def parse_models(payload: Any) -> list[dict[str, Any]]:
    """Chat models only, with id / status / failed."""
    excluded = _excluded_names()
    found: dict[str, dict[str, Any]] = {}
    for row in _rows(payload):
        model_id = str(row.get("id") or row.get("name") or "")
        if not is_chat_model(model_id, excluded):
            continue
        status = row.get("status") if isinstance(row.get("status"), dict) else {}
        item = {
            "id": model_id,
            "status": _status(row),
            "failed": bool(status.get("failed")) if isinstance(status, dict) else False,
        }
        key = _stem(model_id)
        previous = found.get(key)
        # The directory listing is the id /models/load accepts. A preset section
        # named with `.gguf` is a duplicate of that same file.
        if previous is None or previous["id"].lower().endswith(".gguf"):
            found[key] = item
    return list(found.values())


def sync_active(models: list[dict[str, Any]]) -> None:
    """If the router already has one model loaded, chat requests must name it."""
    loaded = [item["id"] for item in models if item["status"] == "loaded"]
    if len(loaded) == 1:
        llm_client.set_active_model(loaded[0])


async def fetch_models(http_client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await http_client.get(
        f"{LLM_BASE_URL.rstrip('/')}/models",
        timeout=llm_client.REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    models = parse_models(response.json())
    sync_active(models)
    return models


async def _post_model(http_client: httpx.AsyncClient, action: str, model_id: str) -> None:
    response = await http_client.post(
        f"{LLM_BASE_URL.rstrip('/')}/models/{action}",
        json={"model": model_id},
        timeout=llm_client.REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()


async def select_model(http_client: httpx.AsyncClient, model_id: str) -> dict[str, Any]:
    """Unload whatever is resident, load `model_id`, and wait until it is ready."""
    models = await fetch_models(http_client)
    known = {item["id"] for item in models}
    if model_id not in known:
        raise LookupError(model_id)
    for item in models:
        if item["status"] in {"loaded", "loading"} and item["id"] != model_id:
            await _post_model(http_client, "unload", item["id"])
    await _post_model(http_client, "load", model_id)
    last = {"id": model_id, "status": "loading", "failed": False}
    for _ in range(_LOAD_POLLS):
        current = await fetch_models(http_client)
        match = next((item for item in current if item["id"] == model_id), None)
        if match is None:
            raise RuntimeError(f"{model_id} disappeared while loading")
        last = match
        if match["status"] == "loaded":
            llm_client.set_active_model(model_id)
            return match
        if match["failed"] or match["status"] == "unloaded":
            # Unload of the previous model can report unloaded for the target
            # before load starts. Only treat unloaded as failure after a failed flag.
            if match["failed"]:
                raise RuntimeError(f"{model_id} failed to load")
        await asyncio.sleep(_POLL_SECONDS)
    raise TimeoutError(f"{model_id} still {last['status']}")
