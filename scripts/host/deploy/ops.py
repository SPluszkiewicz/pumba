"""Ask, chat, logs, train, smoke test, check, export, eval, rotate-token."""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import deploy as d


def cmd_eval(args: argparse.Namespace) -> int:
    """Score lookup on a registered checkout. Does not call the chat model."""
    import eval_checkout

    argv = [args.path, "--questions", args.questions, "--timeout", str(args.timeout)]
    if args.agent:
        argv.extend(["--agent", args.agent])
    if args.out:
        argv.extend(["--out", args.out])
    if args.require_clean:
        argv.append("--require-clean")
    if args.allow_unfinished:
        argv.append("--allow-unfinished")
    return eval_checkout.main(argv)


def _resolve_prompt_host_path(raw: str) -> Path:
    """Absolute host path for a prompt file, WSL-safe when given a Windows drive path."""
    raw = raw.strip()
    if not raw:
        sys.exit("[deploy] FATAL: empty --file path.")
    candidate = Path(raw).expanduser()
    if not candidate.is_file():
        normalized = d._normalize_codebase_path(raw)
        candidate = Path(normalized).expanduser()
    if not candidate.is_file():
        candidate = (d.REPO_ROOT / raw).resolve()
    if not candidate.is_file():
        sys.exit(f"[deploy] FATAL: prompt file not found: {raw}")
    return candidate.resolve()


def cmd_chat(_args: argparse.Namespace) -> int:
    """Interactive multi-turn REPL via the app container (`--repl`)."""
    d._require_podman()
    d._run(["podman", "compose", "run", "--rm", "-it", "app", "--repl"])
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    d._require_podman()
    if getattr(args, "file", None):
        host_path = d._resolve_prompt_host_path(args.file)
        d._run(
            [
                "podman",
                "compose",
                "run",
                "--rm",
                "-v",
                f"{host_path}:/prompt.txt:ro",
                "app",
                "--file",
                "/prompt.txt",
            ]
        )
        return 0

    question = getattr(args, "question", None)
    if not question:
        sys.exit(
            "[deploy] FATAL: pass a question string, or --file <path>, or '-' to read stdin."
        )

    if question == "-":
        prompt = sys.stdin.read()
        if not prompt.strip():
            sys.exit("[deploy] FATAL: stdin prompt is empty.")
        tmp = d.REPO_ROOT / ".prompt.tmp"
        try:
            tmp.write_text(prompt, encoding="utf-8")
            d._run(
                [
                    "podman",
                    "compose",
                    "run",
                    "--rm",
                    "-v",
                    f"{tmp.resolve()}:/prompt.txt:ro",
                    "app",
                    "--file",
                    "/prompt.txt",
                ]
            )
        finally:
            tmp.unlink(missing_ok=True)
        return 0

    d._run(["podman", "compose", "run", "--rm", "app", question])
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    d._require_podman()
    cmd = ["podman", "compose", "logs"]
    if args.follow:
        cmd.append("-f")
    cmd.append(args.service)
    d._run(cmd)
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    d._require_podman()
    d._ensure_env_file()
    env = d._parse_env_file(d.ENV_FILE)

    if d.TRAIN_LOCK_FILE.exists() and not args.force:
        sys.exit(
            f"[deploy] FATAL: {d.TRAIN_LOCK_FILE} exists (training may be in progress). "
            "Remove it manually or pass --force."
        )

    if not d.HF_TRAIN_BASE_MARKER.exists() and not args.dry_run:
        sys.exit(
            "[deploy] FATAL: HF train base missing. Run first:\n"
            "  podman compose --profile setup run --rm fetch-models "
            "python scripts/container/fetch_models.py --hf-train-base"
        )

    d.TRAIN_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    d.TRAIN_LOCK_FILE.write_text("running\n", encoding="utf-8")
    try:
        dataset_cmd = [
            "podman",
            "compose",
            "--profile",
            "dev",
            "run",
            "--rm",
            "devtools",
            "python",
            "services/trainer/build_dataset.py",
        ]
        if args.incremental:
            dataset_cmd.append("--incremental")
        d._run(dataset_cmd)

        if args.dry_run:
            print("[deploy] dry-run: dataset built, skipping GPU training.")
            return 0

        d._run(["podman", "compose", "--profile", "train", "build", "trainer"])
        d._run(["podman", "compose", "--profile", "train", "run", "--rm", "trainer"])

        lora_host = d.MODELS_DIR / "adapters" / "latest" / "adapter.gguf"
        if not lora_host.exists():
            d._run(
                [
                    "podman",
                    "compose",
                    "--profile",
                    "train",
                    "run",
                    "--rm",
                    "trainer",
                    "python",
                    "/workspace/scripts/convert_lora.py",
                ]
            )

        if lora_host.exists() and not env.get("LLM_LORA_PATH"):
            d._write_env_key("LLM_LORA_PATH", d.DEFAULT_LORA_GGUF)
            print(f"[deploy] set LLM_LORA_PATH={d.DEFAULT_LORA_GGUF} in .env")

        if env.get("LLM_SERVE") == "host":
            d._stop_llm_container()
            d._start_host_llm()
        else:
            d._stop_host_llm()
            d._run(["podman", "compose", "up", "-d", "--force-recreate", "--no-deps", "llm"])

        if not args.skip_index:
            d.cmd_index(argparse.Namespace())
    finally:
        d.TRAIN_LOCK_FILE.unlink(missing_ok=True)

    print("[deploy] train complete. LoRA loaded on llm; RAG re-indexed unless --skip-index.")
    return 0


def cmd_test(_args: argparse.Namespace) -> int:
    """Runs the tiny-CPU-model end-to-end smoke test - see
    docs/guides/testing.md's "End-to-end smoke test" section, which this
    mirrors exactly."""
    d._require_podman()
    previous_env = d.ENV_FILE
    d.ENV_FILE = d.REPO_ROOT / ".env.test"
    env_file = ["--env-file", ".env.test"]
    try:
        return _cmd_test_with_env(env_file)
    finally:
        d.ENV_FILE = previous_env


def _cmd_test_with_env(env_file: list[str]) -> int:
    d._run(
        [
            "podman",
            "compose",
            *env_file,
            "--profile",
            "setup",
            "run",
            "--rm",
            "fetch-models",
            "python",
            "scripts/container/fetch_models.py",
            "--test",
        ]
    )
    d._run(["podman", "compose", *env_file, "build", "llm", "embedder", "tools", "agent", "mcp"])
    d._run(["podman", "compose", *env_file, "up", "-d", *d.SMOKE_SERVICES])
    d._wait_healthy(d.SMOKE_SERVICES, timeout=180)
    try:
        d._run(["podman", "compose", *env_file, "--profile", "test", "run", "--rm", "smoke-test"])
    finally:
        d._run(["podman", "compose", *env_file, "down"], check=False)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Run `scripts/host/functional_check.py` against the running stack."""
    import functional_check

    argv = ["--agent", args.agent, "--timeout", str(args.timeout)]
    if args.workspace:
        argv.extend(["--workspace", args.workspace])
    return functional_check.main(argv)


def cmd_export(args: argparse.Namespace) -> int:
    """Zip the repository without models, logs, or local state."""
    import export_repo

    argv = ["--out", args.out]
    if args.include_env:
        argv.append("--include-env")
    return export_repo.main(argv)


def cmd_rotate_token(args: argparse.Namespace) -> int:
    """Generate a new AGENT_BEARER_TOKEN and recreate every holder of it."""
    token = secrets.token_urlsafe(32)
    d._write_env_key("AGENT_BEARER_TOKEN", token)
    print(f"[deploy] wrote a new AGENT_BEARER_TOKEN to {d.ENV_FILE}", flush=True)
    if args.skip_recreate:
        print(
            "[deploy] --skip-recreate: restart agent, app, and mcp yourself so they pick it up",
            flush=True,
        )
        return 0
    if d._host_llm_selected():
        d._run(
            [
                "podman",
                "compose",
                "up",
                "-d",
                "--force-recreate",
                "--no-deps",
                "agent",
                "app",
                "mcp",
                "ui-proxy",
            ],
            cwd=str(d.REPO_ROOT),
        )
        return 0
    d._run(
        ["podman", "compose", "up", "-d", "--force-recreate", "agent", "app", "mcp", "ui-proxy"],
        cwd=str(d.REPO_ROOT),
    )
    return 0
