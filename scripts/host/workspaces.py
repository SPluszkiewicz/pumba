"""Registry of repositories and working copies.

A repository is identified by its remote URL (git origin, or the Subversion
repository root). Every checkout of that URL shares one Qdrant collection.
A second ticket records a path and a mount; it does not start a new index.
Uncommitted edits stay out of that collection and are read from the checkout
that asked the question.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from repo import ROOT as REPO_ROOT
import product

REGISTRY_PATH = REPO_ROOT / "state" / "workspaces.json"
COMPOSE_MOUNTS = REPO_ROOT / "state" / "compose.workspaces.yaml"
INDEX_LOCK = REPO_ROOT / "state" / "index.lock"
_LOCK_STALE_SECONDS = 6 * 60 * 60


_WIN_DRIVE = re.compile(r"^([A-Za-z]):/(.*)$")
_WSL_DRIVE = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")


def running_in_wsl() -> bool:
    """True when this process is a WSL guest, not native Linux."""
    if os.name != "posix":
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "microsoft" in version.lower()


def path_spellings(raw: str) -> list[str]:
    """Spellings of one checkout. A Windows drive and its WSL mount are the same tree.

    `/mnt/<letter>` is only the single-letter WSL drive mount. Any other
    absolute path, including a native Linux directory, is left as written.
    """
    text = raw.strip().replace("\\", "/")
    if len(text) > 1:
        text = text.rstrip("/")
    found: list[str] = []

    def add(item: str) -> None:
        if item and item not in found:
            found.append(item)

    add(text)
    drive = _WIN_DRIVE.match(text)
    if drive:
        letter, rest = drive.group(1), drive.group(2)
        suffix = f"/{rest}" if rest else ""
        add(f"{letter.upper()}:{suffix or '/'}")
        add(f"/mnt/{letter.lower()}{suffix}")
        return found
    mounted = _WSL_DRIVE.match(text)
    if mounted:
        letter, rest = mounted.group(1), mounted.group(2) or ""
        suffix = f"/{rest}" if rest else ""
        add(f"/mnt/{letter.lower()}{suffix}")
        add(f"{letter.upper()}:{suffix or '/'}")
    return found


def engine_bind_path(
    raw: str,
    *,
    runtime: str,
    host_os: str,
    in_wsl: bool | None = None,
    is_dir=None,
) -> str:
    """Path the container engine can bind.

    Docker Desktop on Windows keeps the drive path. Podman on Windows, and
    any engine inside WSL, uses `/mnt/<letter>/...`. Native Linux uses the
    path that already exists and does not invent a `/mnt` directory.
    """
    text = raw.strip().replace("\\", "/")
    wsl = running_in_wsl() if in_wsl is None else in_wsl
    spellings = path_spellings(text)
    check = is_dir if is_dir is not None else (lambda candidate: Path(candidate).is_dir())

    def first(pattern: re.Pattern[str]) -> str | None:
        for candidate in spellings:
            if pattern.match(candidate):
                return candidate
        return None

    if runtime == "docker" and host_os == "nt":
        return first(_WIN_DRIVE) or text
    if host_os == "nt" or wsl:
        return first(_WSL_DRIVE) or text
    for candidate in spellings:
        if check(candidate):
            return candidate
    return text


def normalize_host_path(raw: str) -> str:
    """Compare Windows and POSIX spellings of the same checkout."""
    text = raw.strip().replace("\\", "/")
    if len(text) >= 2 and text[1] == ":":
        text = text[0].lower() + text[1:]
    return text.rstrip("/")


def normalize_remote(url: str) -> str:
    """Canonical remote. Userinfo is dropped so two accounts share one index."""
    text = url.strip()
    if text.startswith("path:"):
        return text
    if text.startswith("git@"):
        host, _, path = text[4:].partition(":")
        text = f"ssh://git@{host}/{path}"
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text.rstrip("/").lower()
    netloc = parts.netloc.lower()
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    path = parts.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


def repo_id_for(url: str) -> str:
    digest = hashlib.sha1(normalize_remote(url).encode()).hexdigest()[:12]
    return f"repo_{digest}"


def checkout_id_for(path: Path) -> str:
    key = normalize_host_path(path.resolve().as_posix())
    digest = hashlib.sha1(key.encode()).hexdigest()[:12]
    return f"co_{digest}"


def _capture(argv: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def detect_remote(path: Path) -> tuple[str, str]:
    """`(vcs, canonical url)`. A tree with no remote is its own repository."""
    if (path / ".svn").is_dir():
        url = _capture(
            ["svn", "info", "--non-interactive", "--show-item", "repos-root-url", str(path)],
            path,
        )
        if url:
            return "svn", normalize_remote(url)
    if (path / ".git").exists():
        url = _capture(["git", "-C", str(path), "remote", "get-url", "origin"], path)
        if url:
            return "git", normalize_remote(url)
        key = normalize_host_path(path.resolve().as_posix())
        return "git", f"path:{key}"
    key = normalize_host_path(path.resolve().as_posix())
    return "none", f"path:{key}"


def empty_registry() -> dict:
    return {"repos": {}, "checkouts": []}


def load_registry(path: Path | None = None) -> dict:
    target = path or REGISTRY_PATH
    if not target.is_file():
        return empty_registry()
    data = json.loads(target.read_text(encoding="utf-8"))
    data.setdefault("repos", {})
    data.setdefault("checkouts", [])
    return data


def save_registry(data: dict, path: Path | None = None) -> None:
    target = path or REGISTRY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Registration:
    checkout_path: Path
    checkout_id: str
    repo_id: str
    collection: str
    vcs: str
    url: str
    mount: str
    needs_index: bool
    new_checkout: bool


def register_checkout(path: Path, *, registry_path: Path | None = None) -> Registration:
    """Record `path`. Index only the first time this remote is seen."""
    resolved = path.resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(resolved)
    vcs, url = detect_remote(resolved)
    repo_id = repo_id_for(url)
    checkout_id = checkout_id_for(resolved)
    mount = f"/checkouts/{checkout_id}"
    data = load_registry(registry_path)
    repos: dict = data["repos"]
    repo = repos.get(repo_id)
    needs_index = True
    if repo is None:
        repos[repo_id] = {
            "id": repo_id,
            "vcs": vcs,
            "url": url,
            "collection": repo_id,
            "indexed": False,
        }
    else:
        needs_index = not bool(repo.get("indexed"))
    new_checkout = True
    host_path = resolved.as_posix()
    for item in data["checkouts"]:
        if item.get("id") == checkout_id:
            new_checkout = False
            item["path"] = host_path
            item["repo_id"] = repo_id
            item["mount"] = mount
            break
    else:
        data["checkouts"].append(
            {
                "id": checkout_id,
                "path": host_path,
                "repo_id": repo_id,
                "mount": mount,
            }
        )
    save_registry(data, registry_path)
    return Registration(
        checkout_path=resolved,
        checkout_id=checkout_id,
        repo_id=repo_id,
        collection=repo_id,
        vcs=vcs,
        url=url,
        mount=mount,
        needs_index=needs_index,
        new_checkout=new_checkout,
    )


def mark_indexed(repo_id: str, *, registry_path: Path | None = None) -> None:
    data = load_registry(registry_path)
    repo = data["repos"].get(repo_id)
    if repo is None:
        return
    repo["indexed"] = True
    save_registry(data, registry_path)


def compose_mounts_text(data: dict, *, host_path_for) -> str:
    """Extra read-only mounts. `host_path_for` maps a checkout path to the engine path."""
    lines = [
        "# Generated by scripts/host/workspaces.py. Do not edit.",
        "services:",
        "  tools:",
        "    volumes:",
    ]
    volumes: list[str] = []
    for item in data.get("checkouts") or []:
        host = host_path_for(str(item["path"]))
        mount = str(item["mount"])
        volumes.append(f'      - "{host}:{mount}:ro"')
    if not volumes:
        return "# No registered checkouts.\nservices: {}\n"
    lines.extend(volumes)
    lines.append("  agent:")
    lines.append("    volumes:")
    lines.extend(volumes)
    writable = [item[: -len(':ro"')] + '"' if item.endswith(':ro"') else item for item in volumes]
    lines.append("  patch:")
    lines.append("    volumes:")
    lines.extend(writable)
    lines.append("")
    return "\n".join(lines)


def write_compose_mounts(data: dict, *, host_path_for, dest: Path | None = None) -> bool:
    """Write the override file. Returns True when the text changed."""
    target = dest or COMPOSE_MOUNTS
    text = compose_mounts_text(data, host_path_for=host_path_for)
    previous = target.read_text(encoding="utf-8") if target.is_file() else ""
    if previous == text:
        return False
    target.write_text(text, encoding="utf-8")
    return True


MCP_SERVER_NAME = product.MCP_SERVER
_LEGACY_MCP_SERVER_NAMES = product.MCP_LEGACY

# Separate from this stack's MCP server. Reads atlassian.env; does not index tickets.
ATLASSIAN_MCP_SERVER = {
    "type": "stdio",
    "command": "uvx",
    "args": [
        "--directory",
        "${workspaceFolder}",
        "--env-file",
        "atlassian.env",
        "mcp-atlassian",
    ],
}


def install_vscode_mcp(workspace: Path, host_path: str) -> None:
    """Point this folder's MCP client at the checkout. Keep a foreign file."""
    dest = workspace / ".vscode" / "mcp.json"
    if dest.is_file():
        try:
            current = json.loads(dest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[deploy] {dest} is not JSON, left unchanged", flush=True)
            return
        servers = current.get("servers") if isinstance(current, dict) else None
        if not isinstance(servers, dict):
            print(f"[deploy] {dest} has no servers map, left unchanged", flush=True)
            return
        found_name = None
        entry = None
        for name in (MCP_SERVER_NAME, *_LEGACY_MCP_SERVER_NAMES):
            candidate = servers.get(name)
            if isinstance(candidate, dict):
                found_name, entry = name, candidate
                break
        if entry is None or found_name is None:
            print(f"[deploy] {dest} has no {MCP_SERVER_NAME} server, left unchanged", flush=True)
            return
        changed = False
        if found_name != MCP_SERVER_NAME:
            servers[MCP_SERVER_NAME] = servers.pop(found_name)
            changed = True
        headers = entry.get("headers")
        if not isinstance(headers, dict):
            headers = {}
            entry["headers"] = headers
        if headers.get("X-Workspace") != host_path:
            headers["X-Workspace"] = host_path
            changed = True
        if servers.get("atlassian") != ATLASSIAN_MCP_SERVER:
            servers["atlassian"] = dict(ATLASSIAN_MCP_SERVER)
            changed = True
        if not changed:
            return
        dest.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"[deploy] updated {dest}", flush=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "servers": {
            MCP_SERVER_NAME: {
                "type": "http",
                "url": "http://127.0.0.1:8092/mcp",
                "headers": {"X-Workspace": host_path},
            },
            "atlassian": dict(ATLASSIAN_MCP_SERVER),
        }
    }
    dest.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(f"[deploy] wrote {dest}", flush=True)


class IndexLock:
    """One indexer at a time. A lock older than six hours is stolen."""

    def __enter__(self) -> "IndexLock":
        INDEX_LOCK.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(INDEX_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - INDEX_LOCK.stat().st_mtime
                except OSError:
                    age = 0
                if age > _LOCK_STALE_SECONDS:
                    INDEX_LOCK.unlink(missing_ok=True)
                    continue
                print("[deploy] index queue: waiting for the other indexer", flush=True)
                time.sleep(2)
                continue
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return self

    def __exit__(self, *_) -> None:
        INDEX_LOCK.unlink(missing_ok=True)
