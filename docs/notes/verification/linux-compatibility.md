# Linux compatibility

> **Status**: informational. Inventory of what the repository declares, requires and exercises for Linux; no distribution is required, only Ubuntu 24.04 (CI, C++ images) and `python:3.11-slim-bookworm` are exercised. Written on 2026-11-07 (file creation date); completed for the `serena` image. · **Last verified**: 2026-11-12

Research note.

## Question

Is this repository compatible with Linux? If so, which families or distributions are actually supported (declared, tested or required), and where does support stop (Windows-only paths, untested distributions, GPU or runtime assumptions)?

## Short answer

Yes. Linux is an intended host, and the native path is described alongside Windows (WSL2) and macOS. The repository does not require a specific distribution to launch the stack: the host must provide Podman or Docker Compose and Python 3.9+. The names that appear for the host are Linux (generic), Debian, Ubuntu, Fedora, and CoreOS for the Podman VM. What is executed is narrower: the CI workflow `runs-on: ubuntu-24.04` with `apt-get`, the C++ and CUDA images on `ubuntu:24.04`, the Python services on `python:3.11-slim-bookworm`, nginx on Alpine. RHEL, Arch and `pacman` are named nowhere. Neither is the family of the WSL `local-llm` distro.

## Declared support

### What the host must be

`docs/getting-started/prerequisites.md`, title "Prerequisites":

> Podman or Docker Compose runs the stack. The command that starts it is `python scripts/deploy.py up`. Host Python 3.9+ is enough for that script; it uses only the standard library.

Same file, section "Container engine":

> Follow the official Podman installation instructions if you are installing Podman on Linux or initializing a Podman machine. On Windows and macOS a machine looks like:

Linux is therefore a Podman installation target, on the same footing as Windows and macOS (Podman machine). No sentence on this page restricts the host to one family.

`CONTRACTS.md`, section "Host prerequisites", repeats the same prerequisite without naming a distribution:

> Podman or Docker Compose runs the containers. The only command to bring the stack up is `python scripts/deploy.py up`.

`scripts/host/deploy/cli.py`, module docstring, sets the Python version on the machine that runs the deployment, including after the WSL relay:

> Python 3.9+ on that machine is enough; a virtualenv is not required to deploy.

`docs/getting-started/first-run.md` names no host system. `docs/architecture/networking.md` describes three Podman networks and names no host OS. `docs/architecture/overview.md` mentions Docker Desktop only to explain why `ui-proxy` publishes a port ("Docker Desktop does not publish a host port for a container that sits only on an internal network"), not as an OS requirement.

### Windows, WSL, and native Linux

The prerequisites page describes a particular Windows setup, section "Container engine":

> On this project's Windows setup, Podman is the WSL2 distro `local-llm`, not the Windows `podman` CLI. [...] Set `DEPLOY_WSL_DISTRO` to use another distro.

The distro's name is `local-llm`. Its family (Ubuntu, Debian, other) is not written. `services/common/product.py` fixes this name:

> WSL_DISTRO = "local-llm"

and the module comment says that this distro "is the Podman VM on the operator's machine, not the repository name".

`scripts/host/deploy/runtime.py`, `_podman_compose_hint`, separates this distro from the default WSL Ubuntu:

> The podman CLI on PATH cannot reach a Linux VM. On this setup Podman runs in the WSL distro local-llm (the default Ubuntu distro is not that VM).

`README.md`, section "Quick start", and `docs/getting-started/installation.md`, section "2. Start the stack", describe the same relay: from PowerShell, a Podman CLI without a machine continues in `local-llm`.

The native filesystem is a separate case. `docs/getting-started/prerequisites.md`, section "Python virtualenv (optional)":

> `scripts/host/fix_permissions.py` is not a setup step. It changes modes on a native Linux filesystem so container uids can read a bind mount. The checkout on `D:` is `/mnt/d` inside WSL, and that mount rejects chmod.

`README.md`, "Quick start":

> `python scripts/host/fix_permissions.py` only matters on a native Linux filesystem; on `D:` seen from WSL as `/mnt/d`, chmod is refused and the script does not prepare the mount.

`CONTRACTS.md`, "Host prerequisites":

> `fix_permissions.py` for a native Linux filesystem

The header of `scripts/host/fix_permissions.py` explains why the Windows chmod is not enough:

> On Windows, chmod has to run inside the Podman machine (or WSL): the host `os.chmod` only toggles the read-only bit and does not change the mode the container sees.

### Debian and Ubuntu on the host (optional venv)

`docs/getting-started/prerequisites.md`, "Python virtualenv (optional)":

> On Debian/Ubuntu, `sudo apt install python3-venv` (or `python3.12-venv`) is required once. Activate with `.venv\Scripts\Activate.ps1` on Windows or `source .venv-wsl/bin/activate` in WSL.

This venv is not the deployment. `docs/getting-started/installation.md`, "Optional: host unit tests":

> The supported toolchain for the services themselves remains the `devtools` container. `scripts/host/fix_permissions.py` is for a native Linux filesystem only.

`scripts/host/install_deps.py`, `explain_venv_failure`, repeats the apt recipe if `ensurepip` is missing:

> this Python cannot seed pip (the Debian/Ubuntu venv package is missing). [...] sudo apt install python3.{version}-venv

### Fedora, Ubuntu, Debian, CoreOS for the GPU

`docs/guides/troubleshooting-gpu.md`, "Automatic CDI setup on `deploy up`":

> On **Fedora** (native Podman, not WSL), install the toolkit once: `sudo dnf install -y nvidia-container-toolkit`

The same page, "Symptom: `llm` never becomes healthy", places the toolkit in the Podman VM, on Windows or macOS, with `rpm-ostree` "or the equivalent for your machine's OS". It does not name this VM's distribution.

`scripts/host/deploy/gpu.py`, `_try_generate_cdi`, gives three recipes when `nvidia-ctk` is absent:

> Fedora: sudo dnf install -y nvidia-container-toolkit
> Ubuntu/Debian: sudo apt install -y nvidia-container-toolkit
> Podman machine (CoreOS): podman machine ssh, then sudo rpm-ostree install nvidia-container-toolkit && reboot

Fedora, Ubuntu and Debian are here installation commands for the NVIDIA toolkit. CoreOS is the `podman machine` VM, not a bare host named elsewhere.

Without a GPU, the same page, "Fallback: just run on CPU", says the project still works, via `config/compose/cpu.yaml`.

### File watching

`docs/concepts/indexing-pipeline.md`, section following the one-shot job (the passage is repeated by `docs/guides/reindexing-the-codebase.md`, "Watch mode"):

> This is best-effort and Linux-preferred. `watchdog` relies on `inotify` on Linux [...] On a native Linux host bind-mounting a real filesystem (no VM hop in between), `--watch` behaves as expected.

`services/indexer/main.py`, module docstring, carries the same sentence: "`--watch` is best-effort and Linux-preferred" and "On native Linux hosts [...] `--watch` works as expected". `services/indexer/requirements.txt` points to this docstring for the `watchdog` package.

### Agent rule, not a distro matrix

`services/agent/rules/platforms.md`:

> Windows and Unix: Source, CMake, and scripts in the tree use LF. [...] Do not assume `python` versus `python3` or `sh` versus PowerShell without saying which host.

This is a style rule for the repository's files. It does not declare a distribution.

### EVO-X5

`docs/guides/evo-x5.md`, title "Deploy on the EVO-X5 Pro", describes the machine (Ryzen AI Max+ PRO 495, Radeon 8065S) and a host `llama-server` on Vulkan. It does not name this machine's OS. It says:

> The CUDA image of the `llm` service cannot see this GPU. `up` runs `llama-server` on the host, on Vulkan, and does not start that container.

and:

> `--backend rocm` prints a probe and `--run` stops.

### SECURITY.md

`SECURITY.md` does not fix a supported platform. It mentions "POSIX- and Windows-style" paths (section "The `tools` service") and, in "Known limitations":

> `--watch` on Windows → WSL2 bind mounts still misses some inotify events.

## Exercised support (CI, images, scripts)

### CI

`.github/workflows/ci.yml`, header comment:

> Needs no model file, no GPU, no Podman: runs on the plain runner with the same pins the devtools image uses (Python 3.11, Ubuntu 24.04 toolchain).

Both jobs carry `runs-on: ubuntu-24.04`. The `unit` job installs the C++ toolchain with:

> sudo apt-get install -y --no-install-recommends build-essential cmake libcurl4-openssl-dev nlohmann-json3-dev catch2 ripgrep clang-format clang-tidy cppcheck

The `smoke` job installs `podman` and `podman-compose` with the same `apt-get`, then runs `python scripts/deploy.py test`. Runner Python: `3.11`. The header comment adds that both commands "work verbatim in GitLab CI or a local cron" without naming a GitLab image. No other workflow names another runner.

### Container images

`CONTRACTS.md`, section "Pinned versions":

> C++ toolchain base (app, tools, devtools) | `ubuntu:24.04` (gcc/g++ 13 default, cmake 3.28.3 default via apt, matches the target deployment OS of the C++ project this stack builds/tests)

> Python (all services) | `python:3.11-slim-bookworm`

> CUDA base (llm, embedder GPU variant) | `nvidia/cuda:12.6.3-devel-ubuntu24.04` (build stage), `nvidia/cuda:12.6.3-runtime-ubuntu24.04` (runtime stage)

> ui-proxy (nginx) | image `docker.io/nginxinc/nginx-unprivileged:1.27-alpine`

The `FROM` lines match: `containers/tools/Containerfile`, `containers/devtools/Containerfile` and `containers/app/Containerfile` start from `ubuntu:24.04`; `containers/llm/Containerfile` and `containers/embedder/Containerfile` choose the Ubuntu 24.04 CUDA base or `ubuntu:24.04` depending on `GPU`; `containers/trainer/Containerfile` starts from `nvidia/cuda:12.6.3-devel-ubuntu24.04`; `containers/agent/Containerfile`, `containers/indexer/Containerfile`, `containers/mcp/Containerfile`, `containers/patch/Containerfile` and `containers/docs/Containerfile` start from `python:3.11-slim-bookworm`. The package manager in these Containerfiles is `apt-get`. The `serena` service has no Containerfile: `compose.yaml` pulls the image `ghcr.io/oraios/serena:2.1.0` (`CONTRACTS.md`, "Pinned versions") and launches `/bin/bash -c` with `.venv/bin/activate`. The repository does not say which distribution this image derives from; it is one more base that this table does not cover.

`containers/tools/Containerfile`, header comment, rules out Ubuntu 22.04:

> ubuntu:24.04 is the pin (rather than 22.04) specifically because it matches the target deployment OS of the real C++ project this stack builds/tests against.

The same file specifies that this image's Python is the apt one, not that of the other services:

> Ubuntu 24.04's default `python3` is 3.12 (currently 3.12.3).

`pyproject.toml`, ruff comment, confirms it:

> `services/tools` runs on Ubuntu 24.04's apt Python (3.12)

`compose.yaml`, service `ui-proxy`:

> image: docker.io/nginxinc/nginx-unprivileged:1.27-alpine

and the healthcheck comments: "Busybox wget ships in nginxinc alpine." Alpine is the OS of this image, not a host.

Qdrant is pinned in `CONTRACTS.md` at `qdrant/qdrant:v1.19.0`. This pin names no distribution.

These bases are the OSes *inside* the containers. The installation pages do not say that the host machine must be Ubuntu 24.04 or Debian bookworm.

### Scripts and OS paths in the code

Shell scripts of the repository, shebang `#!/bin/sh`: `containers/llm/entrypoint.sh`, `containers/embedder/entrypoint.sh`, `config/observability/prometheus-entrypoint.sh`. The last one says:

> Invoked with `sh` so the script need not be executable on a Windows checkout.

No `Makefile`, no `justfile`, no `package.json`, `Cargo.toml` or `go.mod`, no `.ps1` file. `powershell.exe` appears only in `scripts/host/deploy/expose.py` (`firewall_allow_command` / `firewall_remove_command`). `apply_firewall` stops this way when the OS is not Windows:

> if sys.platform != "win32": [...] no Windows firewall on this OS. The relay still drops other addresses.

The WSL relay of `scripts/host/deploy/runtime.py`, `_activate_runtime`, triggers only if `on_windows=os.name == "nt"`. On a non-Windows host, the same module chooses Podman or Docker and does not relaunch `wsl`.

`scripts/host/workspaces.py`, `running_in_wsl`:

> True when this process is a WSL guest, not native Linux.

`engine_bind_path`, in the same file:

> Docker Desktop on Windows keeps the drive path. Podman on Windows, and any engine inside WSL, uses `/mnt/<letter>/...`. Native Linux uses the path that already exists and does not invent a `/mnt` directory.

`scripts/host/evo_x5_serve.py` provides a pinned Vulkan binary for only two keys:

> "win32": "llama-b10216-bin-win-vulkan-x64.zip"
> "linux": "llama-b10216-bin-ubuntu-vulkan-x64.tar.gz"

`ensure_binary` classifies the host this way: `os.name == "nt"` becomes `win32`; `sys.platform.startswith("linux")` becomes `linux`; otherwise the key is `sys.platform`. `install_pinned_server` then answers:

> `pas de binaire {tag} pour {system} / {backend}.` (a French string printed by the script; meaning: "no binary {tag} for {system} / {backend}.")

The Linux binary exercised by this path is therefore the Ubuntu Vulkan archive, not a binary per distribution.

`docs/architecture/agent-loop.md` (cancelling a turn):

> On Linux that is the session group (`killpg`). On Windows it is the tree of descendants.

`services/tools/cancel_group.py` branches on `os.name == "posix"` for `start_new_session` / `killpg`, and has a `_win32` function. The image that runs this service is `ubuntu:24.04` (`containers/tools/Containerfile`).

The `requirements.txt` files have no `sys_platform` marker. `pyproject.toml` declares no OS classifier. `services/indexer/requirements.txt` says that the tree-sitter wheels install without a C compiler on `python:3.11-slim-bookworm`. `services/indexer/chunker.py` mentions "manylinux/musllinux/macOS/Windows" wheels: these are pip wheel platforms, not a list of supported hosts.

### Compose

`compose.yaml` and `config/compose/cpu.yaml`, `docker.yaml`, `docker.cpu.yaml`, `podman.yaml`, `host-llm.yaml` name no host distribution. They distinguish the runtime: CDI `nvidia.com/gpu=all` for Podman, `gpus: all` for Docker (`config/compose/docker.yaml`), devices emptied for CPU (`config/compose/cpu.yaml`). `config/compose/host-llm.yaml` says that on EVO-X5 "The chat model is llama-server on the host" and that `deploy.py up` does not start the `llm` container. `scripts/host/deploy/gpu.py` probes the device with the `ubuntu:24.04` image (`podman run --device ... ubuntu:24.04 true`).

## Limits and silences

- **RHEL, Arch Linux, `pacman`.** No occurrence in the docs, workflows, Containerfiles, manifests or scripts searched. The repository is silent.
- **`serena` image.** Pulled, not built; its base distribution is not written in the repository.
- **Alpine as a host.** Only the nginx `1.27-alpine` image is named (`CONTRACTS.md`, `compose.yaml`).
- **Debian as a version.** The Python image is the tag `python:3.11-slim-bookworm`. The repository does not write "Debian 12" and does not say that the host must be Debian. Debian as a host appears only for `apt` (`python3-venv`, and `nvidia-container-toolkit` in `gpu.py`).
- **Family of `local-llm`.** The name is fixed. `runtime.py` says that WSL's default Ubuntu distro is not this VM. The family of `local-llm` itself is not written.
- **Fedora.** `dnf` recipe for the toolkit, native Podman, outside WSL (`troubleshooting-gpu.md`, `gpu.py`). No CI job, no `fedora` image.
- **Ubuntu 22.04.** Named in `containers/tools/Containerfile` to say that the pin is 24.04 rather than 22.04. It is not a base in use.
- **macOS.** Podman machine and GPU passthrough in `prerequisites.md` and `troubleshooting-gpu.md`. No Vulkan key in `_VULKAN_ASSETS`: a `sys.platform` that is neither Windows nor `linux*` falls through to the "no binary" error.
- **Windows paths in the code.** `powershell.exe` firewall limited to `sys.platform == "win32"` (`expose.py`). `wsl -d` relay limited to `os.name == "nt"` (`runtime.py`). chmod via `podman machine ssh` or `wsl` described for Windows (`fix_permissions.py`). No `.ps1` is versioned. The fact that the workspace used for this reading is win32 is not a declaration of the repository.
- **`--watch`.** Reliable on native Linux without a VM hop; the repository says that the Windows → Podman machine → WSL2 → bind-mount chain does not propagate inotify reliably (`services/indexer/main.py`, `docs/concepts/indexing-pipeline.md`).
- **WSL bind mount.** A checkout on a drive letter (`/mnt/<letter>`) refuses `chmod` (`prerequisites.md`, `README.md`). `install_deps.py` adds `--copies` when the venv path starts with `/mnt/`, because a symbolic link is refused there.
- **GPU.** Probe and Ubuntu 24.04 CUDA images, NVIDIA CDI, documented CPU fallback. EVO-X5: Vulkan on the host, CUDA image unsuited to this GPU, ROCm backend stopped (`docs/guides/evo-x5.md`). The binary downloaded for Linux is the `llama-b10216-bin-ubuntu-vulkan-x64` archive.
- **Pages silent on the host OS.** `docs/getting-started/first-run.md`, `docs/architecture/networking.md`. `docs/architecture/overview.md` goes no further than Docker Desktop for the ports. `SECURITY.md` does not list a supported platform.

## Sources

- `README.md` (Quick start)
- `docs/getting-started/prerequisites.md`
- `docs/getting-started/installation.md`
- `docs/getting-started/first-run.md` (silent on the host OS)
- `docs/guides/troubleshooting-gpu.md`
- `docs/guides/reindexing-the-codebase.md` (Watch mode)
- `docs/guides/evo-x5.md`
- `docs/concepts/indexing-pipeline.md`
- `docs/architecture/overview.md`
- `docs/architecture/networking.md`
- `docs/architecture/agent-loop.md`
- `services/agent/rules/platforms.md`
- `SECURITY.md`
- `CONTRACTS.md` (Pinned versions, Host prerequisites)
- `.github/workflows/ci.yml`
- `compose.yaml`
- `config/compose/cpu.yaml`, `config/compose/docker.yaml`, `config/compose/docker.cpu.yaml`, `config/compose/podman.yaml`, `config/compose/host-llm.yaml`
- `containers/tools/Containerfile`, `containers/devtools/Containerfile`, `containers/app/Containerfile`, `containers/llm/Containerfile`, `containers/embedder/Containerfile`, `containers/trainer/Containerfile`, `containers/agent/Containerfile`, `containers/indexer/Containerfile`, `containers/mcp/Containerfile`, `containers/patch/Containerfile`, `containers/docs/Containerfile`
- `containers/llm/entrypoint.sh`, `containers/embedder/entrypoint.sh`, `config/observability/prometheus-entrypoint.sh`
- `pyproject.toml`
- `services/indexer/requirements.txt`
- `services/indexer/main.py`
- `services/indexer/chunker.py`
- `services/tools/cancel_group.py`
- `services/common/product.py`
- `scripts/host/deploy/cli.py`
- `scripts/host/deploy/runtime.py`
- `scripts/host/deploy/gpu.py`
- `scripts/host/deploy/expose.py`
- `scripts/host/workspaces.py`
- `scripts/host/install_deps.py`
- `scripts/host/fix_permissions.py`
- `scripts/host/evo_x5_serve.py`
- `mkdocs.yml` (consulted for the location, at the time of writing)

Additional search in the repository: `linux`, `ubuntu`, `debian`, `fedora`, `rhel`, `alpine`, `arch`, `windows`, `powershell`, `WSL`, `os.name`, `sys.platform`, `win32`, `darwin`, `apt`, `dnf`, `pacman`. `rhel`, `pacman` and Arch Linux: no hits. No `Makefile`, `justfile`, `.ps1`, `package.json`, `Cargo.toml`, `go.mod`.
