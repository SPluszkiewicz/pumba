# Prerequisites

Podman or Docker Compose runs the stack. The command that starts it is
`python scripts/deploy.py up`. Host Python 3.9+ is enough for that script;
it uses only the standard library. A virtualenv is optional and is only for
host-side unit tests.

## Container engine

`CONTAINER_RUNTIME` in `.env` is `auto`, `podman`, or `docker`. `auto` uses
the first of `podman compose version` and `docker compose version` that
succeeds.

On this project's Windows setup, Podman is the WSL2 distro `local-llm`, not
the Windows `podman` CLI. That CLI has no machine, so `podman compose version`
from PowerShell fails with "Cannot connect to Podman". `python scripts/deploy.py up`
from PowerShell sees that and continues the same command inside `local-llm`
after checking that distro starts (`wsl -d local-llm -e true`). If that
distro is missing, deploy exits with the WSL error instead of looping. You
do not open WSL yourself first. Set `CONTAINER_RUNTIME=docker` when you
want Docker Desktop instead. Set `DEPLOY_WSL_DISTRO` to use another distro.

Follow the [official Podman installation instructions](https://podman.io/docs/installation)
if you are installing Podman on Linux or initializing a Podman machine. On
Windows and macOS a machine looks like:

```bash
podman machine init
podman machine start
```

Inside `local-llm`, check the engine with:

```bash
podman --version
podman compose version
```

## Python virtualenv (optional)

Only if you want to run pytest, ruff, or mypy on the host. Deploy does not
use this virtualenv.

```bash
python scripts/host/install_deps.py
```

On Windows that creates `.venv`. Inside WSL the same script creates
`.venv-wsl`, so it does not overwrite the Windows environment. On
Debian/Ubuntu, `sudo apt install python3-venv` (or `python3.12-venv`) is
required once. Activate with `.venv\Scripts\Activate.ps1` on Windows or
`source .venv-wsl/bin/activate` in WSL.

`scripts/host/fix_permissions.py` is not a setup step. It changes modes on a
native Linux filesystem so container uids can read a bind mount. The checkout
on `D:` is `/mnt/d` inside WSL, and that mount rejects chmod. Skip it here.

## GPU (optional, for a faster `llm` service)

If you have an NVIDIA GPU and want `llm` to run on it instead of the CPU
fallback, see [Troubleshooting GPU](../guides/troubleshooting-gpu.md). Podman
uses NVIDIA CDI. Docker uses `config/compose/docker.yaml` (`gpus: all`), which
`deploy.py` adds when the runtime is Docker. The CPU fallback on Docker also
adds `config/compose/docker.cpu.yaml` so the CDI devices are actually removed.

Without a GPU, everything still works. `deploy.py up` falls back to
`config/compose/cpu.yaml`.

## Disk space

Budget roughly 12 GB for the default 14B weights (Q5_K_M, ~9.8 GB), the
embedding model (a few hundred MB), and the container image layers. The
`evo-x5` profile adds the Mistral Small 4 Q5, about 83 GB. See
[Déployer sur l'EVO-X5](../guides/evo-x5.md).

## Next

[Installation](installation.md) - cloning the repo, configuring `.env`, and
starting the stack.
