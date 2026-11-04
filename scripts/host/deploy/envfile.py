"""`.env` creation, token, and CODEBASE_PATH checks."""

from __future__ import annotations

import os
import re
import secrets
import shutil
import sys
from pathlib import Path

import deploy as d

_WIN_DRIVE_PATH = re.compile(r"^([A-Za-z]):[/\\](.*)$")


def _ensure_env_file() -> None:
    """Create `.env` if needed and replace the example bearer token.

    A first `up` used to copy `.env.example` then exit, which looked like a
    failed install. The token is written to `.env` only; it is not printed.
    """
    if not d.ENV_FILE.exists():
        shutil.copy(d.ENV_EXAMPLE, d.ENV_FILE)
        print(f"[deploy] Created {d.ENV_FILE} from .env.example.", flush=True)
    content = d.ENV_FILE.read_text(encoding="utf-8")
    if d.PLACEHOLDER_TOKEN not in content:
        return
    token = secrets.token_urlsafe(32)
    d.ENV_FILE.write_text(content.replace(d.PLACEHOLDER_TOKEN, token), encoding="utf-8")
    print(
        f"[deploy] Wrote a random AGENT_BEARER_TOKEN to {d.ENV_FILE}. "
        "Paste it into the UI Jeton field once per browser tab.",
        flush=True,
    )


def _parse_env_file(path: Path) -> dict[str, str]:
    """Trivial `KEY=VALUE` parser for `.env`-style files - skips blank lines
    and `#` comments. Not a full dotenv implementation (no quoting, no
    variable expansion) because compose.yaml itself doesn't need one; this
    only has to read back values `podman compose` would substitute."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _host_os() -> str:
    return os.name


def _normalize_codebase_path(raw: str) -> str:
    """Path the running engine can bind.

    A Windows drive becomes `/mnt/<letter>/...` for Podman on Windows and
    inside WSL. Docker Desktop on Windows keeps the drive path. Native Linux
    keeps a path that already exists and does not invent `/mnt/<letter>`.
    """
    import workspaces as registry

    raw = raw.strip()
    if not raw:
        return "./sample"
    return registry.engine_bind_path(raw, runtime=d._RUNTIME, host_os=d._host_os())


def _codebase_path_candidates(raw: str) -> list[Path]:
    """Host paths to probe when checking that CODEBASE_PATH exists."""
    import workspaces as registry

    raw = raw.strip() or "./sample"
    candidates: list[Path] = []

    if raw.startswith("./") or raw.startswith("../"):
        candidates.append((d.REPO_ROOT / raw).resolve())
        return candidates

    seen: set[str] = set()
    for spelling in registry.path_spellings(raw):
        if spelling not in seen:
            seen.add(spelling)
            candidates.append(Path(spelling))
        match = _WIN_DRIVE_PATH.match(spelling)
        if match:
            drive, rest = match.group(1).upper(), match.group(2).replace("/", "\\")
            windows = f"{drive}:\\{rest}"
            if windows not in seen:
                seen.add(windows)
                candidates.append(Path(windows))

    return candidates


def _require_bind_source() -> None:
    """Stop before compose when the checkout is missing.

    The engine creates a missing bind source. That mkdir fails when the
    parent is not writable, and it must not create a mount that was never there.
    """
    import workspaces as registry

    raw = d._parse_env_file(d.ENV_FILE).get("CODEBASE_PATH", "./sample")
    if d._codebase_path_exists(raw):
        chosen = d._normalize_codebase_path(raw)
        given = raw.strip().replace("\\", "/")
        if chosen != given:
            print(f"[deploy] checkout: {chosen}", flush=True)
        return
    tried = "\n".join(f"  {item}" for item in registry.path_spellings(raw))
    sys.exit(
        "[deploy] FATAL: checkout directory not found.\n"
        f"Tried:\n{tried}\n"
        "Set CODEBASE_PATH to a directory that exists on this machine. "
        "A Windows drive letter is mounted as /mnt/<letter>/... under WSL. "
        "Native Linux uses the path as given. "
        "A missing directory is not created."
    )


def _codebase_path_exists(raw: str) -> bool:
    return any(path.is_dir() for path in d._codebase_path_candidates(raw))


def _codebase_root_from_env(env: dict[str, str]) -> Path:
    raw = env.get("CODEBASE_PATH", "./sample")
    if raw.startswith("./") or raw.startswith("../"):
        return (d.REPO_ROOT / raw).resolve()
    for candidate in d._codebase_path_candidates(raw):
        if candidate.is_dir():
            return candidate
    return Path(d._normalize_codebase_path(raw))


def _write_env_key(key: str, value: str) -> None:
    """Set or replace one KEY=VALUE line in `.env` without clobbering others."""
    lines: list[str] = []
    if d.ENV_FILE.exists():
        lines = d.ENV_FILE.read_text(encoding="utf-8").splitlines()
    replaced = False
    out: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    d.ENV_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _existing_codebase(raw: str) -> Path | None:
    for candidate in d._codebase_path_candidates(raw):
        if candidate.is_dir():
            return candidate.resolve()
    return None
