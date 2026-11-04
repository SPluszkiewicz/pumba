"""Host-side lifecycle for the stack. Run it as `python scripts/deploy.py`.

Submodules own one concern each. Names stay on this package so tests and
`fix_permissions.py` keep patching `deploy.ENV_FILE`, `deploy._run`, and the
rest. Implementation functions look those names up here at call time.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Literal

from repo import ROOT as REPO_ROOT
import product

ENV_FILE = REPO_ROOT / ".env"
HOST_LLM_PID = REPO_ROOT / "state" / "host-llm.pid"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MODELS_DIR = REPO_ROOT / "models"
TRAIN_LOCK_FILE = MODELS_DIR / "training" / ".train.lock"
HF_TRAIN_BASE_MARKER = MODELS_DIR / "hf" / "Qwen2.5-7B-Instruct" / "config.json"
DEFAULT_LORA_GGUF = "/models/adapters/latest/adapter.gguf"
CORE_SERVICES = ["qdrant", "llm", "embedder", "reranker", "tools", "agent", "mcp"]
UP_SERVICES = [*CORE_SERVICES, "ui-proxy"]
SMOKE_SERVICES = ["qdrant", "llm", "embedder", "tools", "agent", "mcp"]
PLACEHOLDER_TOKEN = "changeme-generate-a-real-secret"
CDI_GPU_DEVICE = "nvidia.com/gpu=all"
CDI_NVIDIA_YAML = Path("/etc/cdi/nvidia.yaml")
_CPU_FALLBACK_ENV = {
    "LLM_GPU_ARG": "0",
    "EMBEDDER_GPU_ARG": "0",
    "LLM_NGL": "0",
    "EMBEDDER_NGL": "0",
}
_USE_CPU_COMPOSE_OVERRIDE = False
DISK_BUDGET_GIB = 12
_RUNTIME = "podman"
_VALID_RUNTIMES = ("auto", "podman", "docker")
WSL_DISTRO_DEFAULT = product.WSL_DISTRO
_HOST_ONLY_COMMANDS = frozenset({"export", "gpu-profile"})
HOST_LLM_HEALTH_URL = "http://127.0.0.1:8080/health"
GPU_PROFILES = ("16g", "24g", "24g-30b", "48g", "48g-30b", "48g-32b", "evo-x5")
GPU_PROFILE_DIR = REPO_ROOT / "config" / "profiles"
PROFILE_EXTRA = {
    "24g-30b": "coder-30b-q5",
    "48g-30b": "coder-30b-q8",
    "48g-32b": "coder-32b-q5",
    "evo-x5": "mistral-small-4",
}
_MOUNT_DEPENDENT_SERVICES = ["tools", "patch", "agent", "ui-proxy"]

CheckStatus = Literal["ok", "warn", "fail", "skip"]

from deploy.runtime import (  # noqa: E402
    _activate_runtime,
    _command_output,
    _compose_argv_engine,
    _compose_cmd,
    _compose_env,
    _compose_failure_hint,
    _engine_probe,
    _needs_container_engine,
    _podman_compose_hint,
    _requested_runtime,
    _require_podman,
    _run,
    _wsl_distro_ready,
    _wsl_reexec_command,
    choose_runtime,
)
from deploy.envfile import (  # noqa: E402
    _WIN_DRIVE_PATH,
    _codebase_path_candidates,
    _codebase_path_exists,
    _codebase_root_from_env,
    _ensure_env_file,
    _existing_codebase,
    _host_os,
    _normalize_codebase_path,
    _parse_env_file,
    _require_bind_source,
    _write_env_key,
)
from deploy.gpu import (  # noqa: E402
    _cdi_list_contains_gpu,
    _cdi_probe,
    _docker_gpu_probe,
    _enable_cpu_compose_fallback,
    _env_gpu_build_requested,
    _prepare_docker_gpu,
    _prepare_gpu_for_up,
    _try_generate_cdi,
    apply_profile_text,
    cmd_fetch_extra,
    cmd_gpu_profile,
)
from deploy.host_llm import (  # noqa: E402
    _host_llm_alive,
    _host_llm_selected,
    _host_llm_weights_missing,
    _require_host_llm_weights,
    _start_host_llm,
    _stop_host_llm,
    _wait_http,
)
from deploy.compose import (  # noqa: E402
    _compose_build_and_up,
    _parse_compose_ps_health,
    _wait_healthy,
)
from deploy.stack import (  # noqa: E402
    _recreate_mount_dependents,
    _run_repo_index,
    cmd_down,
    cmd_index,
    cmd_status,
    cmd_up,
    cmd_workspace,
)
from deploy.ops import (  # noqa: E402
    _resolve_prompt_host_path,
    cmd_ask,
    cmd_chat,
    cmd_check,
    cmd_eval,
    cmd_export,
    cmd_logs,
    cmd_rotate_token,
    cmd_test,
    cmd_train,
)
from deploy.doctor import (  # noqa: E402
    Check,
    _container_env_var,
    _doctor_check_codebase_path,
    _doctor_check_disk_space,
    _doctor_check_env,
    _doctor_check_gpu,
    _doctor_check_gpu_offload,
    _doctor_check_hf_train_base,
    _doctor_check_lora_adapter,
    _doctor_check_models,
    _doctor_check_podman,
    _doctor_check_services,
    _doctor_check_train_commit,
    _doctor_host_llm_check,
    _env_gpu_arg,
    _gpu_probe_argv,
    _render_html,
    _render_terminal,
    cmd_doctor,
)
from deploy.cli import main  # noqa: E402

# Reachable as `deploy.shutil` / `deploy.time` in tests.
shutil = shutil
time = time
