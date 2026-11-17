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

# isort: off
# `repo` puts services/common on sys.path. `product` is not importable before that.
from repo import ROOT as REPO_ROOT
import product
# isort: on

ENV_FILE = REPO_ROOT / ".env"
HOST_LLM_PID = REPO_ROOT / "state" / "host-llm.pid"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
MODELS_DIR = REPO_ROOT / "models"
TRAIN_LOCK_FILE = MODELS_DIR / "training" / ".train.lock"
HF_TRAIN_BASE_MARKER = MODELS_DIR / "hf" / "Mistral-Small-4-119B-2603" / "config.json"
DEFAULT_LORA_GGUF = "/models/adapters/latest/adapter.gguf"
CORE_SERVICES = ["qdrant", "llm", "embedder", "reranker", "tools", "agent", "mcp", "ui-proxy"]
UP_SERVICES = list(CORE_SERVICES)
SMOKE_SERVICES = ["qdrant", "llm", "embedder", "tools", "agent", "mcp", "ui-proxy"]
# Host llama-server loading Mistral Small 4 Q5 (~83 Go) needs more than the
# default compose health timeout.
HOST_LLM_READY_TIMEOUT = 900
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
_HOST_ONLY_COMMANDS = frozenset({"export", "gpu-profile", "check", "expose"})
HOST_LLM_HEALTH_URL = "http://127.0.0.1:8080/health"
GPU_PROFILES = ("16g", "16g-gptoss", "24g", "24g-30b", "48g", "48g-30b", "48g-32b", "evo-x5")
GPU_PROFILE_DIR = REPO_ROOT / "config" / "profiles"
PROFILE_EXTRA = {
    "16g-gptoss": "gpt-oss-20b",
    "24g-30b": "coder-30b-q5",
    "48g-30b": "coder-30b-q8",
    "48g-32b": "coder-32b-q5",
    "evo-x5": "mistral-small-4",
}
_MOUNT_DEPENDENT_SERVICES = ["tools", "agent", "ui-proxy"]

CheckStatus = Literal["ok", "warn", "fail", "skip"]

from deploy.cli import main  # noqa: E402
from deploy.compose import (  # noqa: E402
    _compose_build_and_up,
    _compose_ps_payload,
    _health_from_compose_ps,
    _parse_compose_ps_health,
    _parse_compose_ps_json,
    _wait_healthy,
    _wait_published_doors,
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
from deploy.expose import cmd_expose  # noqa: E402
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
    _host_llm_health_url,
    _host_llm_port,
    _host_llm_selected,
    _host_llm_weights_missing,
    _require_host_llm_weights,
    _start_host_llm,
    _stop_host_llm,
    _stop_llm_container,
    _wait_http,
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
from deploy.stack import (  # noqa: E402
    _fetch_weights,
    _recreate_mount_dependents,
    _run_repo_index,
    cmd_down,
    cmd_index,
    cmd_status,
    cmd_up,
    cmd_workspace,
)

# Imported so tests and the submodules can patch them on this package.
__all__ = [
    "Check",
    "_WIN_DRIVE_PATH",
    "_activate_runtime",
    "_cdi_list_contains_gpu",
    "_cdi_probe",
    "_codebase_path_candidates",
    "_codebase_path_exists",
    "_codebase_root_from_env",
    "_command_output",
    "_compose_argv_engine",
    "_compose_build_and_up",
    "_compose_cmd",
    "_compose_env",
    "_compose_failure_hint",
    "_compose_ps_payload",
    "_container_env_var",
    "_docker_gpu_probe",
    "_doctor_check_codebase_path",
    "_doctor_check_disk_space",
    "_doctor_check_env",
    "_doctor_check_gpu",
    "_doctor_check_gpu_offload",
    "_doctor_check_hf_train_base",
    "_doctor_check_lora_adapter",
    "_doctor_check_models",
    "_doctor_check_podman",
    "_doctor_check_services",
    "_doctor_check_train_commit",
    "_doctor_host_llm_check",
    "_enable_cpu_compose_fallback",
    "_engine_probe",
    "_ensure_env_file",
    "_env_gpu_arg",
    "_env_gpu_build_requested",
    "_existing_codebase",
    "_fetch_weights",
    "_gpu_probe_argv",
    "_health_from_compose_ps",
    "_host_llm_alive",
    "_host_llm_health_url",
    "_host_llm_port",
    "_host_llm_selected",
    "_host_llm_weights_missing",
    "_host_os",
    "_needs_container_engine",
    "_normalize_codebase_path",
    "_parse_compose_ps_health",
    "_parse_compose_ps_json",
    "_parse_env_file",
    "_podman_compose_hint",
    "_prepare_docker_gpu",
    "_prepare_gpu_for_up",
    "_recreate_mount_dependents",
    "_render_html",
    "_render_terminal",
    "_requested_runtime",
    "_require_bind_source",
    "_require_host_llm_weights",
    "_require_podman",
    "_resolve_prompt_host_path",
    "_run",
    "_run_repo_index",
    "_start_host_llm",
    "_stop_host_llm",
    "_stop_llm_container",
    "_try_generate_cdi",
    "_wait_healthy",
    "_wait_http",
    "_wait_published_doors",
    "_write_env_key",
    "_wsl_distro_ready",
    "_wsl_reexec_command",
    "apply_profile_text",
    "choose_runtime",
    "cmd_ask",
    "cmd_chat",
    "cmd_check",
    "cmd_doctor",
    "cmd_down",
    "cmd_eval",
    "cmd_export",
    "cmd_expose",
    "cmd_fetch_extra",
    "cmd_gpu_profile",
    "cmd_index",
    "cmd_logs",
    "cmd_rotate_token",
    "cmd_status",
    "cmd_test",
    "cmd_train",
    "cmd_up",
    "cmd_workspace",
    "main",
]

# Reachable as `deploy.shutil` / `deploy.time` in tests.
shutil = shutil
time = time
