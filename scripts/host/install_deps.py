#!/usr/bin/env python3
"""Install the Python dependencies used by host-side unit tests and scripts.

Mirrors the unit-job install step in `.github/workflows/ci.yml`. By default
this creates `.venv` at the repo root and installs into it. The C++ toolchain
and the container images stay in Podman (`containers/devtools`, `containers/tools`);
this script does not replace `python scripts/deploy.py up`.

Usage (from the repo root):
    python scripts/host/install_deps.py
    python scripts/host/install_deps.py --dry-run
    python scripts/host/install_deps.py --system
    python scripts/host/install_deps.py --trainer
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_DIR = REPO_ROOT / ".venv"
_WIN_HOME = re.compile(r"^[A-Za-z]:[\\/]")
REQUIREMENT_FILES = (
    "services/agent/requirements.txt",
    "services/indexer/requirements.txt",
    "services/tools/requirements.txt",
    "services/mcp/requirements.txt",
)
TRAINER_REQUIREMENTS = "services/trainer/requirements.txt"
# Same pins as the CI unit job. pytest is also listed in services/tools.
TOOLING_PACKAGES = (
    "pytest==8.3.4",
    "pytest-asyncio==0.25.2",
    "ruff==0.14.10",
    "mypy==1.19.1",
    "pyyaml",
    "huggingface_hub",
)


def venv_python(venv_dir: Path, *, platform: str | None = None) -> Path:
    if (platform or os.name) == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _pyvenv_home(venv_dir: Path) -> str:
    cfg = venv_dir / "pyvenv.cfg"
    if not cfg.is_file():
        return ""
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("home = "):
            return line.split("=", 1)[1].strip()
    return ""


def resolve_venv_dir(root: Path | None = None, *, platform: str | None = None) -> Path:
    """`.venv` when it matches this OS. A Windows venv and a WSL venv must not share a directory."""
    base = (root or REPO_ROOT) / ".venv"
    system = platform or os.name
    home = _pyvenv_home(base)
    if not home:
        return base
    windows_home = _WIN_HOME.match(home) is not None
    foreign = windows_home if system != "nt" else not windows_home
    if not foreign:
        return base
    name = ".venv-win" if system == "nt" else ".venv-wsl"
    return base.parent / name


def venv_create_command(python: Path, dest: Path) -> list[str]:
    """`--copies` avoids symlinks, which a Windows drive mounted at `/mnt` rejects."""
    cmd = [str(python), "-m", "venv"]
    if str(dest).replace("\\", "/").startswith("/mnt/"):
        cmd.append("--copies")
    cmd.append(str(dest))
    return cmd


def explain_venv_failure(output: str) -> str | None:
    if "ensurepip is not available" not in output:
        return None
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return (
        "[install] FATAL: this Python cannot seed pip (the Debian/Ubuntu venv package is missing).\n"
        f"  sudo apt install python3.{version}-venv\n"
        "Then run: python3 scripts/host/install_deps.py"
    )


def requirement_paths(*, trainer: bool) -> list[Path]:
    relative = list(REQUIREMENT_FILES)
    if trainer:
        relative.append(TRAINER_REQUIREMENTS)
    return [REPO_ROOT / name for name in relative]


def install_commands(python: Path, *, trainer: bool, upgrade: bool) -> list[list[str]]:
    """pip invocations, in order. Paths are absolute so the cwd does not matter."""
    pip_install = [str(python), "-m", "pip", "install"]
    if upgrade:
        pip_install.append("--upgrade")
    packages: list[str] = []
    for path in requirement_paths(trainer=trainer):
        packages.extend(["-r", str(path)])
    packages.extend(TOOLING_PACKAGES)
    return [
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        [*pip_install, *packages],
    ]


def planned_commands(
    *,
    system: bool,
    trainer: bool,
    upgrade: bool,
    python: Path | None = None,
    venv_dir: Path | None = None,
) -> list[list[str]]:
    """Full argv list, including `python -m venv` when the default venv is missing."""
    commands: list[list[str]] = []
    target = venv_dir or VENV_DIR
    if system:
        interpreter = python or Path(sys.executable)
    else:
        interpreter = python or venv_python(target)
        if python is None and not interpreter.exists():
            commands.append(venv_create_command(Path(sys.executable), target))
    commands.extend(install_commands(interpreter, trainer=trainer, upgrade=upgrade))
    return commands


def _run(cmd: list[str], *, dry_run: bool) -> None:
    print("[install] $ " + " ".join(cmd), flush=True)
    if dry_run:
        return
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode == 0:
        return
    output = f"{result.stdout or ''}{result.stderr or ''}"
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    explained = explain_venv_failure(output)
    if explained:
        sys.exit(explained)
    sys.exit(f"[install] FATAL: command failed ({result.returncode}): {' '.join(cmd)}")


def _activation_hint(venv_dir: Path) -> str:
    if os.name == "nt":
        return str(venv_dir / "Scripts" / "Activate.ps1")
    return f"source {venv_dir / 'bin' / 'activate'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--system",
        action="store_true",
        help="install into the current interpreter instead of .venv",
    )
    parser.add_argument(
        "--trainer",
        action="store_true",
        help="also install services/trainer/requirements.txt (torch, peft, …)",
    )
    parser.add_argument("--upgrade", action="store_true", help="pass --upgrade when installing requirements")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    args = parser.parse_args(argv)

    missing = [path for path in requirement_paths(trainer=args.trainer) if not path.is_file()]
    if missing:
        joined = "\n".join(f"  {path}" for path in missing)
        sys.exit(f"[install] FATAL: requirement file not found:\n{joined}")

    venv_dir = resolve_venv_dir()
    if not args.system and venv_dir != VENV_DIR:
        print(f"[install] {VENV_DIR.name} belongs to another operating system; using {venv_dir.name}")
    interpreter = venv_python(venv_dir)
    if (
        not args.system
        and not args.dry_run
        and (venv_dir / "pyvenv.cfg").is_file()
        and not interpreter.exists()
    ):
        shutil.rmtree(venv_dir)
    for cmd in planned_commands(
        system=args.system,
        trainer=args.trainer,
        upgrade=args.upgrade,
        venv_dir=venv_dir,
    ):
        _run(cmd, dry_run=args.dry_run)

    if not args.system:
        print(f"[install] activate with: {_activation_hint(venv_dir)}")
    if args.dry_run:
        print("[install] dry-run: nothing was installed.")
    else:
        print("[install] done. Host tests: pytest -q --ignore=tests/integration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
