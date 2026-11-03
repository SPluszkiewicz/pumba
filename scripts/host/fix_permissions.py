#!/usr/bin/env python3
"""Make bind-mounted files readable (and models writable) by container users.

Services run as fixed uids, not as the host user: llm, embedder, tools,
trainer and devtools use uid 1000; indexer and docs use uid 10001. Rootless
Podman maps those uids onto subordinate ids, so a mode-0700 directory the
host can read is invisible inside the container. `a+rX` lets every container
user read a tree; `a+rwX` on `models/` lets fetch-models and trainer write
weights and adapters. `.env` is put back to mode 0600 afterwards.

On Windows, chmod has to run inside the Podman machine (or WSL): the host
`os.chmod` only toggles the read-only bit and does not change the mode the
container sees. Paths like `D:\\rest\\local_llm` are passed as
`/mnt/d/rest/local_llm`, the same mapping `deploy.py` uses for CODEBASE_PATH.

Usage (from the repo root):
    python scripts/host/fix_permissions.py
    python scripts/host/fix_permissions.py --dry-run
    python scripts/host/fix_permissions.py --codebase d:/svn/mon_projet
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import deploy

REPO_ROOT = deploy.REPO_ROOT
MODELS_DIR = REPO_ROOT / "models"
MODEL_SUBDIRS = ("hf", "adapters", "training")
READ_MODE = "a+rX"
WRITE_MODE = "a+rwX"
PRIVATE_MODE = "600"


@dataclass(frozen=True)
class Target:
    path: Path
    writable: bool
    recursive: bool
    label: str


def to_linux_path(path: Path) -> str:
    """Map a Windows drive path to the `/mnt/<drive>/...` form Podman uses."""
    text = str(path)
    match = deploy._WIN_DRIVE_PATH.match(text.replace("\\", "/"))
    if not match:
        return text.replace("\\", "/")
    drive, rest = match.group(1).lower(), match.group(2)
    return f"/mnt/{drive}/{rest}"


def resolve_codebase(raw: str | None) -> Path:
    if raw is None:
        env = deploy._parse_env_file(deploy.ENV_FILE)
        raw = env.get("CODEBASE_PATH", "./sample")
    return deploy._codebase_root_from_env({"CODEBASE_PATH": raw})


def _path_key(path: Path) -> Path:
    return path.resolve() if path.exists() else path.absolute()


def collect_targets(repo: Path, codebase: Path) -> list[Target]:
    """Mounts that must be readable, then `models/` which must also be writable.

    `models/` is applied last so a CODEBASE_PATH that is the repo root does
    not leave the weight directory read-only for the container user. If the
    codebase path *is* `models/`, only the writable mode is applied.
    """
    models = Target(repo / "models", writable=True, recursive=True, label="models")
    reads = [
        Target(codebase, writable=False, recursive=True, label="codebase"),
        Target(repo / "docs", writable=False, recursive=True, label="docs"),
        Target(repo / "mkdocs.yml", writable=False, recursive=False, label="mkdocs.yml"),
    ]
    seen = {_path_key(models.path)}
    unique: list[Target] = []
    for target in reads:
        key = _path_key(target.path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    unique.append(models)
    return unique


def ensure_model_dirs(models_dir: Path, *, dry_run: bool) -> None:
    for name in ("", *MODEL_SUBDIRS):
        path = models_dir / name
        if path.is_dir():
            continue
        if dry_run:
            print(f"[permissions] would create {path}")
            continue
        path.mkdir(parents=True, exist_ok=True)
        print(f"[permissions] directory ready: {path}")


def is_private_file(path: Path) -> bool:
    name = path.name
    return name == ".env" or name.startswith(".env.")


def private_files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in {".git", ".svn"}]
        for name in filenames:
            path = Path(dirpath) / name
            if is_private_file(path):
                found.append(path)
    return found


def command_plan(path: Path, mode: str, *, recursive: bool, platform: str | None = None) -> list[list[str]]:
    """chmod attempts, most likely to honor container uids first."""
    flag = ["-R"] if recursive else []
    if (platform or os.name) == "nt":
        linux = to_linux_path(path)
        return [
            ["podman", "machine", "ssh", "--", "podman", "unshare", "chmod", *flag, mode, linux],
            ["podman", "machine", "ssh", "--", "chmod", *flag, mode, linux],
            ["wsl", "-e", "chmod", *flag, mode, linux],
        ]
    native = str(path)
    if _is_drvfs(native):
        return [["chmod", *flag, mode, native.replace("\\", "/")]]
    return [
        ["podman", "unshare", "chmod", *flag, mode, native],
        ["chmod", *flag, mode, native],
    ]


def _format(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _is_drvfs(path: str) -> bool:
    """A Windows drive mounted in WSL (`/mnt/d/...`). chmod there is not supported."""
    return re.match(r"^/mnt/[a-z](?:/|$)", path.replace("\\", "/")) is not None


def _eperm_only(detail: str) -> bool:
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    return bool(lines) and all("operation not permitted" in line.lower() for line in lines)


def run_plan(commands: list[list[str]], *, dry_run: bool) -> None:
    if not commands:
        sys.exit("[permissions] FATAL: empty chmod plan")
    if dry_run:
        print("[permissions] $ " + _format(commands[0]))
        return
    errors: list[str] = []
    saw_eperm = False
    for cmd in commands:
        print("[permissions] $ " + _format(cmd), flush=True)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            errors.append(f"{cmd[0]}: {exc}")
            continue
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or "").strip()
        if _eperm_only(detail) and _is_drvfs(cmd[-1]):
            saw_eperm = True
            continue
        errors.append(f"{_format(cmd)} -> {result.returncode}: {detail}")
    if saw_eperm and not errors:
        print(
            "[permissions] warning: this Windows drive mount rejects chmod. "
            "Podman on /mnt already sees the files; continuing."
        )
        return
    joined = "\n".join(errors)
    sys.exit(
        "[permissions] FATAL: could not chmod via Podman/WSL.\n"
        f"{joined}\n"
        "On a real Linux filesystem, run this inside the Podman machine. "
        "A path under /mnt/<drive> is a Windows mount: chmod cannot change it."
    )


def apply_target(target: Target, *, dry_run: bool) -> None:
    if not target.path.exists():
        sys.exit(f"[permissions] FATAL: {target.label} path does not exist: {target.path}")
    mode = WRITE_MODE if target.writable else READ_MODE
    print(f"[permissions] {target.label}: {mode} on {target.path}")
    run_plan(command_plan(target.path, mode, recursive=target.recursive), dry_run=dry_run)
    for secret in private_files_under(target.path):
        print(f"[permissions] keep private: {secret}")
        run_plan(command_plan(secret, PRIVATE_MODE, recursive=False), dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--codebase",
        help="host tree mounted as /workspace/app (default: CODEBASE_PATH from .env, else ./sample)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the chmod commands without running them")
    args = parser.parse_args(argv)

    codebase = resolve_codebase(args.codebase)
    ensure_model_dirs(MODELS_DIR, dry_run=args.dry_run)
    for target in collect_targets(REPO_ROOT, codebase):
        if target.label == "models" and args.dry_run and not target.path.exists():
            print(f"[permissions] models: {WRITE_MODE} on {target.path} (created on a real run)")
            run_plan(command_plan(target.path, WRITE_MODE, recursive=True), dry_run=True)
            continue
        apply_target(target, dry_run=args.dry_run)
    print("[permissions] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
