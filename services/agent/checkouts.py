"""Resolve a host checkout path to its repository collection and mount."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

REGISTRY_PATH = Path(os.getenv("WORKSPACES_REGISTRY", "/workspace/workspaces.json"))
TOOLS_HTTP_TIMEOUT = float(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "14400"))
_NOTE_LIMIT = 4000


class UnknownWorkspace(LookupError):
    """The path is not in the registry. The question is refused."""


@dataclass(frozen=True)
class Checkout:
    id: str
    path: str
    repo_id: str
    mount: str
    vcs: str
    collection: str


def normalize_host_path(raw: str) -> str:
    text = raw.strip().replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        text = text[0].lower() + text[1:]
    return text.rstrip("/")


def checkout_keys(raw: str) -> set[str]:
    """Windows `d:/...` and WSL `/mnt/d/...` name the same checkout."""
    text = normalize_host_path(raw)
    keys = {text} if text else set()
    if len(text) >= 2 and text[1] == ":":
        rest = text[2:]
        keys.add(f"/mnt/{text[0]}{rest}")
    if text.startswith("/mnt/") and len(text) > 5 and text[5].isalpha():
        letter = text[5].lower()
        rest = text[6:] if len(text) > 6 and text[6] == "/" else ""
        if len(text) == 6 or rest:
            keys.add(f"{letter}:{rest or '/'}")
    return keys


def load_registry(path: Path | None = None) -> dict:
    target = path or REGISTRY_PATH
    if not target.is_file():
        return {"repos": {}, "checkouts": []}
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"repos": {}, "checkouts": []}
    data.setdefault("repos", {})
    data.setdefault("checkouts", [])
    return data


def resolve_workspace(raw: str, *, registry_path: Path | None = None) -> Checkout:
    keys = checkout_keys(raw)
    if not keys:
        raise UnknownWorkspace("empty workspace path")
    data = load_registry(registry_path)
    repos = data.get("repos") or {}
    for item in data.get("checkouts") or []:
        if not isinstance(item, dict):
            continue
        if not (checkout_keys(str(item.get("path") or "")) & keys):
            continue
        repo = repos.get(item.get("repo_id"))
        if not isinstance(repo, dict):
            raise UnknownWorkspace(raw)
        return Checkout(
            id=str(item.get("id") or ""),
            path=str(item.get("path") or ""),
            repo_id=str(item.get("repo_id") or ""),
            mount=str(item.get("mount") or ""),
            vcs=str(repo.get("vcs") or "none"),
            collection=str(repo.get("collection") or item.get("repo_id") or ""),
        )
    raise UnknownWorkspace(raw)


_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp"}


def is_overlay_source(path: str) -> bool:
    """C and C++ paths only. The shared index is for those files."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name == "CMakeLists.txt":
        return True
    return Path(name).suffix.lower() in _SOURCE_SUFFIXES


def parse_status_paths(vcs: str, stdout: str) -> list[str]:
    """Paths from `git status --porcelain=v1` or `svn status`. Deleted paths are omitted."""
    if vcs == "git":
        return _git_status_paths(stdout)
    if vcs == "svn":
        return _svn_status_paths(stdout)
    return []


def _git_status_paths(stdout: str) -> list[str]:
    paths: list[str] = []
    for line in stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        if status in {"!!", "  "} or "D" in status:
            continue
        rest = line[3:].strip().replace("\\", "/")
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1].strip()
        if rest.startswith('"') and rest.endswith('"'):
            rest = rest[1:-1]
        if rest and is_overlay_source(rest):
            paths.append(rest)
    return paths


def _svn_status_paths(stdout: str) -> list[str]:
    paths: list[str] = []
    for line in stdout.splitlines():
        if not line or line[0] not in {"M", "A", "R", "?"}:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        rest = parts[1].strip().replace("\\", "/")
        if rest.endswith("/"):
            continue
        if is_overlay_source(rest):
            paths.append(rest)
    return paths


def local_changes_note(vcs: str, stdout: str) -> str:
    """Prompt note. Dirty files are not in the shared collection."""
    body = stdout.strip()
    if len(body) > _NOTE_LIMIT:
        body = body[:_NOTE_LIMIT] + "\n…(truncated)"
    lead = (
        "This checkout shares the repository index with every other ticket. "
        "Retrieved chunks can come from another revision. Read the file before trusting them."
    )
    if not body:
        return lead + "\nNo uncommitted changes in this checkout."
    return lead + "\nUncommitted changes in this checkout (not written into the shared index):\n" + body


async def fetch_local_note(
    http_client: httpx.AsyncClient,
    request_id: str,
    checkout: Checkout,
    *,
    tools_base_url: str,
) -> str:
    """svn status / git status of this checkout only. Failures stay a short note."""
    if checkout.vcs == "svn":
        route = "/svn/status"
    elif checkout.vcs == "git":
        route = "/git/status"
    else:
        return local_changes_note(checkout.vcs, "")
    headers = {"X-Request-Id": request_id, "X-Project-Root": checkout.mount}
    try:
        response = await http_client.post(
            f"{tools_base_url.rstrip('/')}{route}",
            json={},
            headers=headers,
            timeout=TOOLS_HTTP_TIMEOUT,
        )
        body = response.json()
        stdout = body.get("stdout") if isinstance(body, dict) else ""
    except (httpx.HTTPError, ValueError, TypeError):
        stdout = ""
    return local_changes_note(checkout.vcs, str(stdout or ""))
