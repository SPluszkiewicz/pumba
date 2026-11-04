"""`doctor` diagnostics. One Check per probe; `cmd_doctor` only sequences them."""

from __future__ import annotations

import argparse
import html
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import deploy as d
import product


@dataclass
class Check:
    """One diagnostic result for `doctor`. `hint` is only shown for
    non-"ok" statuses - it should be an actionable next step, ideally a
    command to run or a doc to read, not a restatement of `detail`."""

    name: str
    status: d.CheckStatus
    detail: str
    hint: str | None = None


def _doctor_check_podman() -> list[Check]:
    engine = d._RUNTIME
    checks: list[Check] = []
    if d.shutil.which(engine) is None:
        checks.append(
            Check(
                engine,
                "fail",
                "not found on PATH",
                hint="Install it first - see docs/getting-started/prerequisites.md.",
            )
        )
        return checks

    version = subprocess.run([engine, "--version"], capture_output=True, text=True)
    checks.append(Check(engine, "ok", version.stdout.strip() or "installed, version unknown"))

    compose = subprocess.run([engine, "compose", "version"], capture_output=True, text=True)
    if compose.returncode == 0:
        detail = compose.stdout.strip().splitlines()[0] if compose.stdout.strip() else "available"
        checks.append(Check(f"{engine} compose", "ok", detail))
    else:
        detail = d._command_output(compose) or f"`{engine} compose version` failed"
        checks.append(
            Check(
                f"{engine} compose",
                "fail",
                detail,
                hint=d._compose_failure_hint(engine, detail),
            )
        )
    return checks


def _doctor_check_env() -> list[Check]:
    if not d.ENV_FILE.exists():
        return [
            Check(
                ".env",
                "fail",
                f"missing at {d.ENV_FILE}",
                hint="python scripts/deploy.py up  # copies .env.example and writes a bearer token",
            )
        ]

    checks = [Check(".env", "ok", f"present at {d.ENV_FILE}")]
    env = d._parse_env_file(d.ENV_FILE)
    token = env.get("AGENT_BEARER_TOKEN", "")
    if not token:
        checks.append(
            Check(
                "AGENT_BEARER_TOKEN",
                "fail",
                "not set in .env",
                hint="python scripts/deploy.py up",
            )
        )
    elif token == d.PLACEHOLDER_TOKEN:
        checks.append(
            Check(
                "AGENT_BEARER_TOKEN",
                "fail",
                "still the placeholder value",
                hint="python scripts/deploy.py up  # replaces the placeholder with a random token",
            )
        )
    else:
        checks.append(Check("AGENT_BEARER_TOKEN", "ok", "set to a non-placeholder value"))
    return checks


def _doctor_check_codebase_path(env: dict[str, str]) -> Check:
    raw = env.get("CODEBASE_PATH", "./sample")
    normalized = d._normalize_codebase_path(raw)
    if d._codebase_path_exists(raw):
        detail = f"{raw} -> {normalized} (directory exists)"
        if raw != normalized:
            detail = f"{raw} (compose uses {normalized})"
        return Check("CODEBASE_PATH", "ok", detail)

    import workspaces as registry

    spellings = registry.path_spellings(raw)
    detail = f"directory not found for {raw!r} (compose would use {normalized})"
    if len(spellings) > 1:
        detail = f"directory not found. Tried {' and '.join(spellings)}"
    return Check(
        "CODEBASE_PATH",
        "fail",
        detail,
        hint=(
            "Set CODEBASE_PATH to a directory that exists on this machine. "
            "A Windows drive letter is mounted as /mnt/<letter>/... under WSL. "
            "Native Linux uses the path as given."
        ),
    )


def _doctor_check_models(env: dict[str, str], *, verify_checksums: bool) -> list[Check]:
    known_specs: dict[str, Any] = {}
    fetch_models: Any = None
    if verify_checksums:
        try:
            import fetch_models as _fetch_models

            fetch_models = _fetch_models
        except ImportError:
            pass
        if fetch_models is not None:
            for spec in (
                fetch_models.CHAT_MODEL,
                fetch_models.EMBED_MODEL,
                fetch_models.TEST_MODEL,
                fetch_models.RERANK_MODEL,
                *fetch_models.EXTRA_MODELS.values(),
            ):
                known_specs[spec.filename] = spec
                known_specs[Path(spec.filename).name] = spec

    checks: list[Check] = []
    for env_var in ("LLM_MODEL_PATH", "EMBEDDER_MODEL_PATH", "RERANKER_MODEL_PATH"):
        raw = env.get(env_var, "")
        if not raw:
            checks.append(Check(env_var, "skip", "not set in .env"))
            continue

        filename = Path(raw).name
        if raw.startswith("/models/"):
            relative = raw.removeprefix("/models/").lstrip("/")
            path = d.MODELS_DIR / (relative or filename)
        else:
            relative = filename
            path = d.MODELS_DIR / filename
        if not path.exists():
            checks.append(
                Check(
                    filename,
                    "fail",
                    f"missing at {path}",
                    hint="Run `python scripts/deploy.py up` or `podman compose --profile setup run --rm fetch-models`.",
                )
            )
            continue

        size_mib = path.stat().st_size / (1024 * 1024)
        detail = f"{size_mib:,.1f} MiB at {path}"
        if not verify_checksums:
            checks.append(Check(filename, "ok", detail))
            continue

        spec = known_specs.get(relative) or known_specs.get(filename)
        if spec is None:
            checks.append(Check(filename, "skip", f"{detail} (not a pinned filename, skipping checksum)"))
        elif spec.sha256 is None:
            checks.append(Check(filename, "skip", f"{detail} (no pinned checksum for this model)"))
        else:
            actual = fetch_models._sha256_of(path)
            if actual == spec.sha256:
                checks.append(Check(filename, "ok", f"{detail}, checksum verified"))
            else:
                checks.append(
                    Check(
                        filename,
                        "fail",
                        f"{detail}, CHECKSUM MISMATCH (expected {spec.sha256}, got {actual})",
                        hint="Re-download it - see scripts/container/fetch_models.py --force.",
                    )
                )
    return checks


def _doctor_check_hf_train_base() -> Check:
    if d.HF_TRAIN_BASE_MARKER.exists():
        return Check("HF train base", "ok", f"present at {d.HF_TRAIN_BASE_MARKER.parent}")
    return Check(
        "HF train base",
        "warn",
        f"missing ({d.HF_TRAIN_BASE_MARKER.parent})",
        hint=(
            "podman compose --profile setup run --rm fetch-models "
            "python scripts/container/fetch_models.py --hf-train-base"
        ),
    )


def _doctor_check_lora_adapter(env: dict[str, str]) -> Check:
    raw = env.get("LLM_LORA_PATH", "").strip()
    if not raw:
        return Check("LLM_LORA_PATH", "skip", "not set (no LoRA adapter loaded)")
    if raw.startswith("/models/"):
        host_path = d.MODELS_DIR / raw.removeprefix("/models/").lstrip("/")
    else:
        host_path = Path(raw)
    if host_path.is_file():
        size_mib = host_path.stat().st_size / (1024 * 1024)
        return Check("LLM_LORA_PATH", "ok", f"{size_mib:,.1f} MiB at {host_path}")
    return Check(
        "LLM_LORA_PATH",
        "fail",
        f"configured but missing on host: {host_path}",
        hint="python scripts/deploy.py train",
    )


def _doctor_check_train_commit(env: dict[str, str]) -> Check:
    manifest_path = d.MODELS_DIR / "adapters" / "latest" / "manifest.json"
    if not manifest_path.exists():
        return Check("train commit", "skip", "no adapter manifest (run deploy.py train)")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return Check("train commit", "warn", "could not read adapter manifest")

    trained = manifest.get("commit_sha", "")
    codebase = d._codebase_root_from_env(env)
    try:
        result = subprocess.run(
            ["git", "-C", str(codebase), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return Check("train commit", "skip", "could not run git in CODEBASE_PATH")

    if result.returncode != 0:
        return Check("train commit", "skip", "CODEBASE_PATH is not a git repo")

    head = result.stdout.strip()
    if trained == head:
        return Check("train commit", "ok", f"adapter trained at {head[:12]}")
    return Check(
        "train commit",
        "warn",
        f"adapter at {trained[:12] if trained else '?'}, HEAD is {head[:12]}",
        hint="python scripts/deploy.py train --incremental",
    )


def _doctor_check_disk_space() -> Check:
    usage = d.shutil.disk_usage(d.REPO_ROOT)
    free_gib = usage.free / (1024**3)
    if free_gib < d.DISK_BUDGET_GIB:
        return Check(
            "disk space",
            "warn",
            f"{free_gib:.1f} GiB free on the filesystem holding {d.REPO_ROOT}",
            hint=f"docs/getting-started/prerequisites.md budgets ~{d.DISK_BUDGET_GIB} GiB for models + image layers.",
        )
    return Check("disk space", "ok", f"{free_gib:.1f} GiB free on the filesystem holding {d.REPO_ROOT}")


def _doctor_host_llm_check() -> Check:
    if d.HOST_LLM_PID.is_file():
        raw = d.HOST_LLM_PID.read_text(encoding="utf-8").strip()
        if raw.isdigit() and d._host_llm_alive(int(raw)):
            return Check("llm", "ok", f"host llama-server (pid {raw})")
    return Check(
        "llm",
        "warn",
        "host llama-server not running (LLM_SERVE=host)",
        hint="python scripts/deploy.py up",
    )


def _doctor_check_services() -> list[Check]:
    host = d._host_llm_selected()
    try:
        result = subprocess.run(
            ["podman", "compose", "ps"],
            cwd=d.REPO_ROOT,
            capture_output=True,
            text=True,
            env=d._compose_env(),
        )
    except OSError as exc:
        return [
            Check(
                "services",
                "fail",
                f"could not run `podman compose ps`: {exc}",
                hint="Install Podman - see docs/getting-started/prerequisites.md.",
            )
        ]
    if result.returncode != 0:
        return [
            Check(
                "services",
                "fail",
                "`podman compose ps` failed",
                hint=(result.stderr or "").strip()[:300] or None,
            )
        ]

    statuses = d._parse_compose_ps_health(result.stdout, d.CORE_SERVICES)
    checks: list[Check] = []
    for service in d.CORE_SERVICES:
        if host and service == "llm":
            checks.append(d._doctor_host_llm_check())
            continue
        status = statuses[service]
        if status == "healthy":
            checks.append(Check(service, "ok", "healthy"))
        elif status == "starting":
            checks.append(Check(service, "warn", "starting", hint="Give it more time, then re-run doctor."))
        elif status == "unhealthy":
            checks.append(
                Check(service, "fail", "unhealthy", hint=f"podman compose logs {service}")
            )
        elif status == "not_found":
            checks.append(
                Check(
                    service,
                    "warn",
                    "not running",
                    hint="python scripts/deploy.py up (or `podman compose up -d " + service + "`).",
                )
            )
        else:
            checks.append(Check(service, "warn", "running, health status unknown"))
    return checks


def _container_env_var(service: str, var: str) -> str | None:
    """Read one env var from a running compose service, or None if unavailable."""
    try:
        result = subprocess.run(
            ["podman", "compose", "exec", "-T", service, "printenv", var],
            cwd=d.REPO_ROOT,
            capture_output=True,
            text=True,
            env=d._compose_env(),
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if value else None


def _env_gpu_arg(env: dict[str, str], key: str, default: str = "1") -> str:
    raw = env.get(key, default).strip()
    return raw if raw else default


def _doctor_check_gpu_offload(env: dict[str, str]) -> list[Check]:
    """Warn when .env requests GPU builds but running containers were built CPU-only."""
    checks: list[Check] = []
    specs = (
        ("llm", "LLM_GPU_ARG", "LLM_GPU_BUILD", "LLM_NGL"),
        ("embedder", "EMBEDDER_GPU_ARG", "EMBEDDER_GPU_BUILD", "EMBEDDER_NGL"),
    )
    for service, arg_key, build_key, ngl_key in specs:
        want_gpu = d._env_gpu_arg(env, arg_key) != "0"
        if not want_gpu:
            checks.append(Check(f"{service} GPU build", "skip", f"{arg_key}=0 in .env (CPU intended)"))
            continue

        build_val = d._container_env_var(service, build_key)
        if build_val is None:
            checks.append(
                Check(
                    f"{service} GPU build",
                    "skip",
                    f"{service} not running (cannot read {build_key})",
                )
            )
            continue

        ngl_val = d._container_env_var(service, ngl_key)
        if build_val == "0":
            checks.append(
                Check(
                    f"{service} GPU build",
                    "warn",
                    f"{build_key}=0 in container but {arg_key}=1 in .env",
                    hint=f"podman compose build {service} && podman compose up -d --force-recreate {service}",
                )
            )
        elif ngl_val == "0":
            checks.append(
                Check(
                    f"{service} GPU offload",
                    "warn",
                    f"{ngl_key}=0 (all layers on CPU)",
                    hint=f"Set {ngl_key}=999 in .env and recreate {service}.",
                )
            )
        else:
            detail = f"{build_key}={build_val}"
            if ngl_val:
                detail += f", {ngl_key}={ngl_val}"
            checks.append(Check(f"{service} GPU offload", "ok", detail))
    return checks


def _gpu_probe_argv() -> list[str]:
    if d._RUNTIME == "docker":
        return ["docker", "run", "--rm", "--gpus", "all", "ubuntu:24.04", "nvidia-smi"]
    return ["podman", "run", "--rm", "--device", "nvidia.com/gpu=all", "ubuntu:24.04", "nvidia-smi"]


def _doctor_check_gpu() -> Check:
    argv = d._gpu_probe_argv()
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        return Check(
            "gpu",
            "fail",
            f"could not run {argv[0]}: {exc}",
            hint="See docs/getting-started/prerequisites.md.",
        )
    if result.returncode == 0:
        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "ok"
        return Check("gpu", "ok", first_line)
    shown = " ".join(argv)
    return Check(
        "gpu",
        "fail",
        f"GPU probe failed (`{shown}`)",
        hint="See docs/guides/troubleshooting-gpu.md - or ignore this if you intend to run CPU-only.",
    )


def _render_terminal(checks: list[Check]) -> None:
    markers = {"ok": "[OK]  ", "warn": "[WARN]", "fail": "[FAIL]", "skip": "[SKIP]"}
    for check in checks:
        marker = markers[check.status]
        print(f"{marker} {check.name}: {check.detail}")
        if check.status != "ok" and check.hint:
            print(f"       hint: {check.hint}")

    counts = {status: sum(1 for c in checks if c.status == status) for status in ("ok", "warn", "fail", "skip")}
    print(
        f"\n[deploy] doctor summary: {counts['ok']} ok, {counts['warn']} warn, "
        f"{counts['fail']} fail, {counts['skip']} skip"
    )


def _render_html(checks: list[Check]) -> str:
    """Single self-contained HTML string (inline CSS, no JS, no external
    assets) - a point-in-time snapshot, not a live dashboard. Re-run
    `doctor --html` to refresh it."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    colors = {"ok": "#1a7f37", "warn": "#9a6700", "fail": "#cf222e", "skip": "#57606a"}
    rows = []
    for check in checks:
        hint_html = (
            f'<div class="hint">{html.escape(check.hint)}</div>' if check.hint and check.status != "ok" else ""
        )
        rows.append(
            f'<tr class="status-{check.status}">'
            f'<td><span class="badge">{check.status.upper()}</span></td>'
            f"<td>{html.escape(check.name)}</td>"
            f"<td>{html.escape(check.detail)}{hint_html}</td>"
            f"</tr>"
        )

    counts = {status: sum(1 for c in checks if c.status == status) for status in ("ok", "warn", "fail", "skip")}
    badge_css = "\n".join(f".status-{status} .badge {{ background: {color}; }}" for status, color in colors.items())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{product.NAME} doctor report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #1f2328; background: #fff; }}
  h1 {{ font-size: 1.4rem; }}
  .meta {{ color: #57606a; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #d0d7de; vertical-align: top; }}
  th {{ background: #f6f8fa; }}
  .badge {{ display: inline-block; color: #fff; border-radius: 4px; padding: 0.15rem 0.5rem;
           font-size: 0.8rem; font-weight: 600; }}
  .hint {{ color: #57606a; font-size: 0.85rem; margin-top: 0.25rem; }}
  {badge_css}
</style>
</head>
<body>
<h1>{product.NAME} doctor report</h1>
<p class="meta">Generated {generated_at}. This is a snapshot - re-run
<code>python scripts/deploy.py doctor --html</code> to refresh it.</p>
<p class="meta">Summary: {counts['ok']} ok, {counts['warn']} warn, {counts['fail']} fail, {counts['skip']} skip</p>
<table>
<thead><tr><th>Status</th><th>Check</th><th>Detail</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>
"""


def cmd_doctor(args: argparse.Namespace) -> int:
    env = d._parse_env_file(d.ENV_FILE)

    checks: list[Check] = []
    checks.extend(d._doctor_check_podman())
    checks.extend(d._doctor_check_env())
    checks.append(d._doctor_check_codebase_path(env))
    checks.extend(d._doctor_check_models(env, verify_checksums=args.verify_checksums))
    checks.append(d._doctor_check_hf_train_base())
    checks.append(d._doctor_check_lora_adapter(env))
    checks.append(d._doctor_check_train_commit(env))
    checks.append(d._doctor_check_disk_space())
    checks.extend(d._doctor_check_services())
    checks.extend(d._doctor_check_gpu_offload(env))
    if args.gpu:
        checks.append(d._doctor_check_gpu())

    d._render_terminal(checks)

    if args.html:
        html_path = Path(args.html)
        html_path.write_text(d._render_html(checks), encoding="utf-8")
        print(f"\n[deploy] HTML report written to {html_path.resolve()}")

    return 1 if any(c.status == "fail" for c in checks) else 0
