"""Argparse entry for `python scripts/deploy.py`."""

from __future__ import annotations

import argparse
import subprocess
import sys

import deploy as d
import product

_USAGE = """Host-side lifecycle for the stack. Run it as `python scripts/deploy.py`
(that file forwards here). Scripts under `scripts/container/` run inside a
container and are started by Compose.

`CONTAINER_RUNTIME` in `.env` is `auto` (working Podman, otherwise Docker),
`podman`, or `docker`. On Windows, if the Podman CLI cannot reach a machine
and Docker is not the one selected, the same command continues inside the
WSL distro named in `product.WSL_DISTRO`. Python 3.9+ on that machine is
enough; a virtualenv is not required to deploy.

This script never invents new behavior: every step is exactly one of the
commands from the docs, just sequenced and with health/error checking added.

Usage (from the repo root):
    python scripts/deploy.py up             # first-time or repeat full deploy
    python scripts/deploy.py up --skip-models --skip-index
    python scripts/deploy.py status
    python scripts/deploy.py index
    python scripts/deploy.py workspace d:/svn/mon_projet
    python scripts/deploy.py eval D:/rest/opencv --questions scripts/host/eval_opencv_questions.json
    python scripts/deploy.py train            # QLoRA on CODEBASE_PATH, reload LoRA on llm
    python scripts/deploy.py ask "How does the indexer chunk C++ files?"
    python scripts/deploy.py ask --file my-question.md
    python scripts/deploy.py chat           # interactive REPL (SSE + conversation)
    python scripts/deploy.py logs agent --follow
    python scripts/deploy.py test           # tiny-model end-to-end smoke test
    python scripts/deploy.py doctor         # diagnose the whole stack
    python scripts/deploy.py doctor --html doctor-report.html
    python scripts/deploy.py rotate-token
    python scripts/deploy.py export          # zip the repo without models/logs/state
    python scripts/deploy.py down
    python scripts/deploy.py down --volumes
"""


def main() -> int:
    d._activate_runtime(sys.argv[1:])
    parser = argparse.ArgumentParser(description=_USAGE, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    up = subparsers.add_parser("up", help="fetch models, build, start, wait healthy, index")
    up.add_argument("--skip-models", action="store_true", help="skip the fetch-models step")
    up.add_argument("--skip-index", action="store_true", help="skip running the indexer")
    up.add_argument("--timeout", type=int, default=300, help="seconds to wait for health (default 300)")
    up.set_defaults(func=d.cmd_up)

    down = subparsers.add_parser("down", help="stop the stack")
    down.add_argument("--volumes", action="store_true", help="also remove the qdrant-data volume")
    down.set_defaults(func=d.cmd_down)

    subparsers.add_parser("status", help="podman compose ps").set_defaults(func=d.cmd_status)
    subparsers.add_parser(
        "index", help="recreate tools mount from CODEBASE_PATH, then re-index"
    ).set_defaults(func=d.cmd_index)

    workspace = subparsers.add_parser(
        "workspace",
        help="register a checkout; one index per remote URL, mount every registered copy",
    )
    workspace.add_argument("path", help="host checkout to register, e.g. d:/svn/ticket-142")
    workspace.add_argument(
        "--update",
        action="store_true",
        help="incremental reindex of this tree into the shared repository collection",
    )
    workspace.set_defaults(func=d.cmd_workspace)

    eval_cmd = subparsers.add_parser(
        "eval",
        help="retrieval baseline on a registered checkout (lookup only, no chat model)",
    )
    eval_cmd.add_argument("path", help="registered checkout, e.g. D:/rest/opencv")
    eval_cmd.add_argument("--questions", required=True, help="JSON question file")
    eval_cmd.add_argument("--agent", default=None, help="agent base URL (default http://127.0.0.1:8090)")
    eval_cmd.add_argument("--timeout", type=float, default=120.0)
    eval_cmd.add_argument("--out", default=None, help="also write the report JSON")
    eval_cmd.add_argument(
        "--require-clean",
        action="store_true",
        help="exit non-zero when git status is not empty",
    )
    eval_cmd.add_argument(
        "--allow-unfinished",
        action="store_true",
        help="score even when the registry does not mark the index finished",
    )
    eval_cmd.set_defaults(func=d.cmd_eval)

    train = subparsers.add_parser(
        "train",
        help="build QLoRA dataset from CODEBASE_PATH, fine-tune, convert LoRA, reload llm",
    )
    train.add_argument(
        "--incremental",
        action="store_true",
        help="dataset from git diff since last trained commit",
    )
    train.add_argument(
        "--dry-run",
        action="store_true",
        help="build dataset only, skip GPU training",
    )
    train.add_argument(
        "--skip-index",
        action="store_true",
        help="do not re-run the indexer after training",
    )
    train.add_argument(
        "--force",
        action="store_true",
        help="ignore an existing training lock file",
    )
    train.set_defaults(func=d.cmd_train)

    ask = subparsers.add_parser(
        "ask",
        help="ask the agent a question via the app CLI (string, --file, or '-' for stdin)",
    )
    ask.add_argument(
        "question",
        nargs="?",
        default=None,
        help="question text, or '-' to read the prompt from stdin",
    )
    ask.add_argument(
        "-f",
        "--file",
        metavar="PATH",
        help="read the prompt from a file (for long prompts; avoids shell ARG_MAX limits)",
    )
    ask.set_defaults(func=d.cmd_ask)

    subparsers.add_parser("chat", help="interactive multi-turn REPL (streams tokens)").set_defaults(
        func=d.cmd_chat
    )

    logs = subparsers.add_parser("logs", help="podman compose logs <service>")
    logs.add_argument("service")
    logs.add_argument("--follow", action="store_true")
    logs.set_defaults(func=d.cmd_logs)

    subparsers.add_parser(
        "test", help="run the tiny-model end-to-end smoke test (no GPU/large downloads needed)"
    ).set_defaults(func=d.cmd_test)

    check_cmd = subparsers.add_parser(
        "check", help="run the 9-step functional check suite against the running stack"
    )
    check_cmd.add_argument("--agent", default="http://127.0.0.1:8090", help="agent base URL")
    check_cmd.add_argument("--workspace", default=None, help="registered workspace path (default: sample/)")
    check_cmd.add_argument("--timeout", type=float, default=120.0)
    check_cmd.set_defaults(func=d.cmd_check)

    doctor = subparsers.add_parser(
        "doctor", help="diagnose prereqs, .env, models, disk space, and service health"
    )
    doctor.add_argument("--html", metavar="PATH", help="also write a static HTML report to PATH")
    doctor.add_argument(
        "--gpu", action="store_true", help="also probe GPU access (slow, pulls ubuntu:24.04 if not cached)"
    )
    doctor.add_argument(
        "--verify-checksums",
        action="store_true",
        help="hash pinned model files and compare against scripts/container/fetch_models.py's checksums",
    )
    doctor.set_defaults(func=d.cmd_doctor)

    gpu_profile = subparsers.add_parser(
        "gpu-profile",
        help="write one GPU case into .env (14B, a larger coder, or the EVO-X5 host model); does not start containers",
    )
    gpu_profile.add_argument("name", choices=d.GPU_PROFILES)
    gpu_profile.set_defaults(func=d.cmd_gpu_profile)

    fetch_extra = subparsers.add_parser(
        "fetch-extra",
        help="download the optional GGUF for 24g-30b, 48g-30b, 48g-32b, or evo-x5",
    )
    fetch_extra.add_argument("name", choices=tuple(d.PROFILE_EXTRA))
    fetch_extra.set_defaults(func=d.cmd_fetch_extra)

    rotate = subparsers.add_parser(
        "rotate-token", help="regenerate AGENT_BEARER_TOKEN and recreate agent, app, and mcp"
    )
    rotate.add_argument("--skip-recreate", action="store_true", help="only rewrite .env")
    rotate.set_defaults(func=d.cmd_rotate_token)

    export_cmd = subparsers.add_parser(
        "export",
        help="zip the repository without models, logs, state/, or virtualenvs",
    )
    export_cmd.add_argument(
        "--out",
        default=str(d.REPO_ROOT / f"{product.ARCHIVE_ROOT}-export.zip"),
        help="destination zip path",
    )
    export_cmd.add_argument(
        "--include-env",
        action="store_true",
        help="also pack .env and atlassian.env (secrets). Default is to omit them.",
    )
    export_cmd.set_defaults(func=d.cmd_export)

    args = parser.parse_args()
    try:
        return args.func(args)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1
