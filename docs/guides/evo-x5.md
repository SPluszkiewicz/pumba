# Deploy on the EVO-X5 Pro

GMKtec EVO-X5 Pro: Ryzen AI Max+ PRO 495, Radeon 8065S, 192 GB of unified
LPDDR5X. The profile is called `evo-x5`. It loads Mistral Small 4 in Q5
(~83 GB, three parts), alias `mistral-small-4`.

The CUDA image of the `llm` service cannot see this GPU. `up` runs
`llama-server` on the host, on Vulkan, and does not start that container.

Set P-MODE to Performance (120 W sustained). Silent and Balanced lower
the sustained clock.

## On the machine

`up` uses `llama-server` if it is on the PATH. Otherwise it installs the Vulkan binary `b10216` in `state/llama-server`. Then, from the repository root:

```text
python scripts/deploy.py gpu-profile evo-x5
python scripts/deploy.py up
```

`gpu-profile` writes `config/profiles/evo-x5.env` into `.env`, once per
machine. It does not talk to Podman. `up` downloads the three shards if
they are missing, starts the stack, and waits for `llama-server`'s `/health` on the host
(`up` waits up to 900 s, even if `--timeout` is shorter).
It does not start the `llm` container. `down` stops `llama-server`
on the host along with the stack.
`python scripts/deploy.py fetch-extra evo-x5` remains available separately.

!!! warning "`serena` is not started by `up` in this mode"
    In this mode, `up` starts the services with `compose up -d --no-deps` without
    `llm`. The `depends_on` of `agent` is therefore not followed and the
    `serena` sidecar does not start (see [the Serena section](#serena)).

## Measuring

The CUDA profile campaign recreates the `llm` container. Here the chat is
the host process. The same campaign, on this profile alone, applies
`evo-x5`, recreates `agent` so that it reads `LLM_BASE_URL`, stops the
`llm` container if it is running, starts `llama-server` if it is not already there, and
does not start the `llm` container. Before starting, `--run` copies `.env`
to `state/profile-campaign.env.bak` and restores it at the end; `llama-server`
stays running after the campaign. It first sends a "ping" (`pong`), then
the questions.

```text
python scripts/host/profile_campaign.py --profiles evo-x5
python scripts/host/profile_campaign.py --run --profiles evo-x5 --workspace D:/path/to/the/copy
```

The copy is already registered
(`python scripts/deploy.py workspace <path>`). The questions are in
`scripts/host/evo_campaign_prompts.json`, an array of `{id, prompt}`.
The identifiers `S1` to `S5` keep the sample grid. Any other
identifier records the clock, the `/metrics` counters of
`llama-server`, and the answer. The report is
`state/profile-campaign.json`. Loading the 119B can take the
`up` timeout (fifteen minutes); `--load-timeout` does not shorten it
below that.

The profile sets `LLM_SERVE=host`, `LLM_GPU_ARG=0`, a context of 262,144
(the model's maximum, 256 × 1024), the `q8_0` cache, 16 threads.
The same number is the agent's budget. The embedder and the reranker
stay on the CPU (`EMBEDDER_NGL=0`, `RERANKER_NGL=0`). Putting them on
the Radeon takes bus bandwidth from the chat model. The NPU (55 TOPS) does not load
these GGUFs: it stays free for a gate. `POST /review` calls the chat
model, not a second model. `LLM_LORA_PATH` is not applied: the host's
`llama-server` is started without `--lora` (see
[Fine-tuning](fine-tuning-the-codebase.md)). Prometheus does not scrape this
process: with the `obs` override, the `llm` target stays failing (see
[Observability](observability.md)).

### Switching back to an NVIDIA profile

A 14B profile puts `LLM_SERVE=container` and `LLM_BASE_URL=http://llm:8080` back.
But `gpu-profile` only writes the keys present in the profile. The keys
set by `evo-x5` therefore stay in `.env`: `LLM_GPU_ARG=0` (the `llm` image
would be built without GPU), `LLM_THREADS=16`, `LLM_THREADS_BATCH=16` and
`AGENT_CONTEXT_BUDGET_TOKENS=262144` (while `LLM_CTX_SIZE` goes back to
32768). Put them back by hand to the values from `.env.example` before `up`:
`LLM_GPU_ARG=1`, `LLM_THREADS=` and `LLM_THREADS_BATCH=` empty,
`AGENT_CONTEXT_BUDGET_TOKENS=28544`.

Without a binary on the PATH, `up` downloads it (Vulkan, tag `b10216`). The three shards are under
`models/UD-Q5_K_M/`. The server listens on all interfaces of
this machine, so that the `agent` container can call it. The page
(`127.0.0.1:8090`) and the MCP (`127.0.0.1:8092`) stay on loopback.
Do not forward port 8080 on the router if the model must not be
reachable from the network.

`--fit` is not in the command: on this APU it sends experts back
to the CPU. Vulkan is the engine. `--backend rocm` prints a probe
and `--run` stops.

## VS Code

On the same machine, after `up`:

```text
python scripts/deploy.py workspace <path-of-the-copy>
```

This writes `.vscode/mcp.json`. The `pumba` server is
`http://127.0.0.1:8092/mcp`, with the `X-Workspace` header set to that
copy. The tools are `lookup`, `check`, `calls`, `types`, `ask`. They do not
modify files.

The agent's page is `http://127.0.0.1:8090/ui`. The token is
`AGENT_BEARER_TOKEN`. An edit request goes through Serena's write tools
(so through the `serena` sidecar, see below): the agent is then
in the editing state until a successful `build_step`, with no commit.

## Serena

The `serena` container (clangd, write access to the copy) serves the agent's
read and write named tools. In host mode, `up` does not start it,
as noted above, and `doctor` does not probe it. Start it once
after `up`, with the same `-f` files as `deploy.py` (add
`state/compose.workspaces.yaml` if you registered a copy with
`workspace`):

```text
podman compose -f compose.yaml -f config/compose/podman.yaml -f config/compose/host-llm.yaml -f state/compose.workspaces.yaml up -d --no-deps serena
```

Without `serena`, the agent's Serena tools have no server to call.
It also does not follow mount changes: recreate it after
`workspace` on a new path (see
[Reindexing](reindexing-the-codebase.md)).

For the editor to talk to the model directly (Continue.dev), the
OpenAI-compatible client uses `http://127.0.0.1:8080/v1` and the identifier
`mistral-small-4`. The full `config.yaml` file is in
[VS Code and Continue.dev](vscode.md). The editor then applies
the diff. The MCP does not.
