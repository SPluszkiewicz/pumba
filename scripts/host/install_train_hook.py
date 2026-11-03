#!/usr/bin/env python3
"""Install the optional post-commit QLoRA training hook into CODEBASE_PATH.

Reads CODEBASE_PATH from `.env` (same rules as deploy.py). Copies
scripts/git-hooks/post-commit.train.sample to .git/hooks/post-commit when
the target repo is a git work tree.

Usage:
    python scripts/host/install_train_hook.py
    python scripts/host/install_train_hook.py --remove
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
SAMPLE_HOOK = REPO_ROOT / "scripts" / "git-hooks" / "post-commit.train.sample"


def _parse_env_file(path: Path) -> dict[str, str]:
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


def _codebase_root(env: dict[str, str]) -> Path:
    raw = env.get("CODEBASE_PATH", "./app")
    if raw.startswith("./") or raw.startswith("../"):
        return (REPO_ROOT / raw).resolve()
    return Path(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true", help="remove the installed hook")
    args = parser.parse_args()

    env = _parse_env_file(ENV_FILE)
    root = _codebase_root(env)
    git_dir = root / ".git"
    if not git_dir.exists():
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            print(f"[install_train_hook] FATAL: not a git repo: {root}", file=sys.stderr)
            return 1
        git_dir = Path(probe.stdout.strip())
        if not git_dir.is_absolute():
            git_dir = (root / git_dir).resolve()

    hooks_dir = git_dir / "hooks" if git_dir.is_dir() else root / ".git" / "hooks"
    if git_dir.is_file():  # worktree: gitdir pointer
        gitdir = git_dir.read_text(encoding="utf-8").strip().removeprefix("gitdir: ").strip()
        hooks_dir = Path(gitdir) / "hooks"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / "post-commit"

    if args.remove:
        if dest.exists():
            dest.unlink()
            print(f"[install_train_hook] removed {dest}")
        else:
            print(f"[install_train_hook] no hook at {dest}")
        return 0

    if not SAMPLE_HOOK.exists():
        print(f"[install_train_hook] FATAL: sample missing: {SAMPLE_HOOK}", file=sys.stderr)
        return 1

    shutil.copy2(SAMPLE_HOOK, dest)
    dest.chmod(dest.stat().st_mode | 0o111)
    print(f"[install_train_hook] installed {dest}")
    print("[install_train_hook] edit the hook if your WSL distro or repo path differs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
