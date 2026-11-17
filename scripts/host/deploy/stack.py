"""Bring the stack up, down, and index a checkout."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import deploy as d


def _fetch_weights(*, host: bool) -> None:
    """Download GGUFs. A failed fetch keeps files already on disk; say how to resume."""
    try:
        d._run(["podman", "compose", "--profile", "setup", "run", "--rm", "fetch-models"])
    except subprocess.CalledProcessError:
        sys.exit(
            "[deploy] FATAL: downloading the default models failed.\n"
            "Files already in models/ are kept. Re-run:\n"
            "  python scripts/deploy.py up\n"
            "or skip the download if the GGUFs are already there:\n"
            "  python scripts/deploy.py up --skip-models"
        )
    if not host or not d._host_llm_weights_missing():
        return
    print("[deploy] default fetch does not include the EVO chat GGUF; fetching it.", flush=True)
    try:
        d.cmd_fetch_extra(argparse.Namespace(name="evo-x5"))
    except subprocess.CalledProcessError:
        sys.exit(
            "[deploy] FATAL: downloading the EVO chat GGUF failed.\n"
            "Retry:\n"
            "  python scripts/deploy.py fetch-extra evo-x5\n"
            "  python scripts/deploy.py up --skip-models"
        )


def cmd_up(args: argparse.Namespace) -> int:
    d._require_podman()
    d._ensure_env_file()
    d._require_bind_source()
    env = d._parse_env_file(d.ENV_FILE)
    host = env.get("LLM_SERVE") == "host"
    d._prepare_gpu_for_up(env)

    if not args.skip_models:
        d._fetch_weights(host=host)

    if host:
        d._require_host_llm_weights()
        d._stop_llm_container()
        d._start_host_llm(timeout=max(args.timeout, d.HOST_LLM_READY_TIMEOUT), wait=False)
    else:
        d._stop_host_llm()
    d._compose_build_and_up(host=host)
    waited = [name for name in d.CORE_SERVICES if not (host and name == "llm")]
    d._wait_healthy(waited, args.timeout)
    d._wait_published_doors(host=host, timeout=args.timeout)

    if not args.skip_index:
        try:
            d._run(["podman", "compose", "--profile", "index", "run", "--rm", "indexer"])
        except subprocess.CalledProcessError:
            print(
                "[deploy] Stack is up; indexing failed. "
                "Fix the error, then: python scripts/deploy.py index",
                file=sys.stderr,
            )
            return 1

    print(
        '[deploy] Done. Try: python scripts/deploy.py ask "How does the indexer chunk C++ files?"'
    )
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    d._require_podman()
    d._stop_host_llm()
    cmd = ["podman", "compose", "down"]
    if args.volumes:
        cmd.append("-v")
    d._run(cmd)
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    d._require_podman()
    d._run(["podman", "compose", "ps"])
    return 0


def _recreate_mount_dependents() -> None:
    d._run(
        [
            "podman",
            "compose",
            "up",
            "-d",
            "--force-recreate",
            "--no-deps",
            *d._MOUNT_DEPENDENT_SERVICES,
        ]
    )


def cmd_index(_args: argparse.Namespace) -> int:
    d._require_podman()
    d._require_bind_source()
    d._recreate_mount_dependents()
    d._run(["podman", "compose", "--profile", "index", "run", "--rm", "indexer"])
    return 0


def _run_repo_index(checkout: Path, collection: str, checkout_id: str = "") -> None:
    """Index one checkout into its repository collection. Does not touch .env."""
    import workspaces as registry

    env = d._compose_env()
    env["CODEBASE_PATH"] = registry.engine_bind_path(
        checkout.as_posix(), runtime=d._RUNTIME, host_os=os.name
    )
    env["QDRANT_COLLECTION"] = collection
    env["INDEXER_SOURCE_ROOT"] = "/workspace/app"
    if checkout_id:
        env["INDEXER_CHECKOUT_ID"] = checkout_id
    d._run(
        ["podman", "compose", "--profile", "index", "run", "--rm", "--no-deps", "indexer"],
        env=env,
    )


def cmd_workspace(args: argparse.Namespace) -> int:
    """Register a checkout by its remote URL and mount it next to the others.

    The first checkout of a remote is indexed. Another ticket of the same
    remote only records the path. `--update` runs the incremental indexer
    for this tree into the shared collection.
    """
    import workspaces as registry

    found = d._existing_codebase(args.path)
    if found is None:
        tried = "\n".join(f"  {item}" for item in registry.path_spellings(args.path))
        sys.exit(
            "[deploy] FATAL: workspace directory not found.\n"
            f"Tried:\n{tried}\n"
            "Pass a directory that exists on this machine. "
            "A Windows drive letter is mounted as /mnt/<letter>/... under WSL. "
            "Native Linux uses the path as given."
        )
    recorded = registry.register_checkout(found)
    registry.install_vscode_mcp(recorded.checkout_path, recorded.checkout_path.as_posix())
    print(
        f"[deploy] {recorded.checkout_path.as_posix()} -> {recorded.collection} ({recorded.vcs})",
        flush=True,
    )
    changed = registry.write_compose_mounts(
        registry.load_registry(),
        host_path_for=lambda path: registry.engine_bind_path(
            path, runtime=d._RUNTIME, host_os=os.name
        ),
    )
    d._require_podman()
    if changed:
        d._recreate_mount_dependents()
    if recorded.needs_index or recorded.new_checkout or args.update:
        with registry.IndexLock():
            d._run_repo_index(recorded.checkout_path, recorded.collection, recorded.checkout_id)
        registry.mark_indexed(recorded.repo_id)
    else:
        print("[deploy] repository already indexed; local edits stay out of the index", flush=True)
    return 0
