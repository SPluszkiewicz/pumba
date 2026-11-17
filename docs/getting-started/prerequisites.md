# Prerequisites

Podman or Docker Compose runs the stack. The command that starts it is
`python scripts/deploy.py up`. Host Python 3.9+ is enough for that script;
it uses only the standard library. A virtualenv is optional and is only for
host-side unit tests.

The first `up` needs internet: it downloads the model weights (Hugging Face,
from a container), the base images, and the `serena` sidecar image
(`ghcr.io/oraios/serena:2.1.0`). After that the stack runs offline.

## Container engine

`CONTAINER_RUNTIME` in `.env` is `auto`, `podman`, or `docker`. `auto` picks,
in this order:

1. Podman, if `podman compose version` succeeds;
2. otherwise Docker, if `docker compose version` succeeds;
3. otherwise, on Windows only, if the Podman CLI fails with a connection
   error (no machine), the WSL distro `local-llm` (below).

`podman` and `docker` skip that probing and use the named engine. Docker
Desktop on the same Windows machine therefore wins over the WSL distro
under `auto`; set `CONTAINER_RUNTIME=podman` to force the Podman path.

On this project's Windows setup, Podman is the WSL2 distro `local-llm`, not
the Windows `podman` CLI. That CLI has no machine, so `podman compose version`
from PowerShell fails with "Cannot connect to Podman". When the engine
resolves to Podman, `python scripts/deploy.py <command>` from PowerShell sees
that and continues the same command inside `local-llm` after checking that
distro starts (`wsl -d local-llm -e true`). If that distro is missing, deploy
exits with the WSL error instead of looping. You do not open WSL yourself
first. Set `DEPLOY_WSL_DISTRO` to use another distro. `export`,
`gpu-profile`, `check`, and `expose` only touch the host and never need an
engine or the WSL hand-off.

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

That creates `.venv` at the repository root and installs the dependencies of
`agent`, `indexer`, `tools`, and `mcp` plus the CI tooling (pytest,
pytest-asyncio, ruff, mypy, pyyaml, huggingface_hub). `--dry-run` prints
the commands; `--system` installs into the current interpreter instead;
`--trainer` adds the trainer requirements (torch, peft, ...). If `.venv` already exists but was created by the other
OS (a Windows `.venv` seen from WSL, or the reverse), the script creates
`.venv-wsl` in WSL or `.venv-win` on Windows instead of overwriting it. On
Debian/Ubuntu, `sudo apt install python3-venv` (or `python3.12-venv`) is
required once. Activate with `.venv\Scripts\Activate.ps1` on Windows or
`source .venv-wsl/bin/activate` (`.venv/bin/activate` when it is the one
created) in WSL.

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

Budget roughly 12 GiB (the threshold `doctor` warns under) for the default
14B weights (Q5_K_M, ~9.8 GiB), the embedding model (a few hundred MiB), the
reranker (~606 MiB), and the container image layers. Optional models
come on top (`python scripts/deploy.py fetch-extra <profile>`):

| Profile | Extra download |
|---|---|
| `16g-gptoss` | gpt-oss-20b MXFP4, ~11.3 GiB |
| `24g-30b` | Qwen3-Coder-30B-A3B Q5_K_M, ~20.2 GiB |
| `48g-30b` | Qwen3-Coder-30B-A3B Q8_0, ~30.3 GiB |
| `48g-32b` | Qwen2.5-Coder-32B Q5_K_M, ~21.7 GiB |
| `evo-x5` | Mistral Small 4 UD-Q5_K_M (three shards), ~83 GiB. See [Deploy on the EVO-X5](../guides/evo-x5.md). |

QLoRA training also needs the Hugging Face base (~242 GiB), see
[Fine-tuning](../guides/fine-tuning-the-codebase.md).

## Next

[Installation](installation.md), cloning the repo, configuring `.env`, and
starting the stack.
