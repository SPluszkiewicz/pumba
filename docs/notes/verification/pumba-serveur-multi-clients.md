# Pumba on one server, several workstations

> **Status**: informative, partial state (none of the gaps below is filled: no accounts, `expose` relay absent from the guides). Written on 2026-11-07 (creation date of the file); the passages on writing and the `serena` sidecar are updated. · **Last verified**: 2026-11-12

**Verdict: partial.** Today the stack starts on a server machine, a remote workstation can reach it through a documented SSH tunnel, and a host relay accepts a list of IPv4 addresses; the Compose ports stay on `127.0.0.1`, there are no user accounts, and the code being queried must already be a checkout registered on the server.

Research note. The guides in `docs/` describe how to use the product. The code, Compose, and the tests own the behavior. When a guide and the code diverge, both are cited separately.

## What "server + several workstations" requires

| Need | What the repository does |
| --- | --- |
| Start the stack on a machine that is not the user's workstation | `python scripts/deploy.py up` starts the stack on the host that runs the command. Nothing in this command requires the browser or the editor to be on the same machine. The ports published by Compose, however, only listen on `127.0.0.1` of that host. |
| Reach this stack from another machine | Two paths exist. The VS Code guide describes a local SSH tunnel from the editor's workstation. The code of `deploy.py expose` opens, on a LAN address of the host, a TCP relay to those same loopback ports, for a list of IPv4 addresses. |
| Several workstations at the same time | The relay's address list can contain several IPv4 addresses. The MCP HTTP protocol is sessionless. The agent accepts several `conversation_id`s. The shipped cap is global: two `/chat` at a time, one `llama-server` slot, one token for the whole API. |
| Send a request (page, API, MCP) | `GET /ui`, `POST /chat`, and `POST /chat/stream` go through `ui-proxy`. The Streamable HTTP MCP is `POST /mcp`. The chat model is the OpenAI API on port 8080. `lookup` / `check` / `calls` / `types` / `ask` are the MCP tools. |
| Query the workstation's code | Not shipped as an automatic checkout. `X-Workspace` designates a path already registered on the stack's machine. An unknown path receives HTTP 400. |

## Network publication

Compose publishes the page, the MCP, and the chat model on loopback only.

- `ui-proxy`: `"127.0.0.1:${AGENT_PORT:-8090}:8090"` (`compose.yaml`, service `ui-proxy`). The comment above it says that this proxy is the loopback entry point to `agent`, because Docker Desktop does not publish a host port for a container that is only on an internal network.
- `mcp`: `"127.0.0.1:${MCP_PORT:-8092}:${MCP_PORT:-8092}"` (`compose.yaml`, service `mcp`).
- `llm`: `"127.0.0.1:${LLM_PORT:-8080}:8080"` (`compose.yaml`, service `llm`).
- Grafana, `obs` profile, not started by `up`: `"127.0.0.1:3000:3000"` (`config/compose/observability.yaml`). `docs/guides/observability.md`, section "Observability", says that none of this is published outside the host.

`CONTRACTS.md`, table "Ports", repeats these publications: `127.0.0.1:8080`, `127.0.0.1:8090` via `ui-proxy`, `127.0.0.1:8092`. The same section says not to publish the MCP on `0.0.0.0`. `docs/architecture/networking.md`, sections `client-net` and "What's *not* on any of these networks", says the same for `8092`, `8090`, and `8080`. `docs/index.md`, "Architecture at a glance", says that the host reaches the page, the MCP, and the chat model on `127.0.0.1` only.

Inside the containers, the listening is wider than the host publication. `services/mcp/server.py`, `FastMCP` constructor, sets `host="0.0.0.0"`. `containers/llm/entrypoint.sh` starts `llama-server` with `--host 0.0.0.0`. `containers/host-proxy/nginx.conf` does `listen 8090` without an address. That is the listening inside the container. The `ports:` line of Compose is what reaches the host, and it is `127.0.0.1`.

`agent` has no `ports:` in `compose.yaml`. `docs/getting-started/first-run.md` says so: the page is `http://127.0.0.1:8090/ui` through `ui-proxy`, and `agent` itself is not published. The embedder, reranker, Qdrant, `tools`, `patch`, and `serena` are not published (`compose.yaml`; `CONTRACTS.md`, port rows 8081, 8082, 6333, 8091, 8093; `serena` has no row in this table, it has no `ports:` and is only on `tools-net`).

### `evo-x5` profile

On this profile, the `llm` container is not started. `config/compose/host-llm.yaml` empties its `ports` (`ports: !override []`) and puts `agent` on `publish-net` so that it calls `llama-server` on the host. `scripts/host/evo_x5_serve.py`, `serve_argv`, passes `--host 0.0.0.0`. The docstring of the file says that the page and the MCP stay on `127.0.0.1`. `docs/guides/evo-x5.md`, section "On the machine", says that the server listens on all the interfaces of that machine so that the `agent` container can call it, and that the page (`127.0.0.1:8090`) and the MCP (`127.0.0.1:8092`) stay on loopback. The same page says not to forward port 8080 on the router if the model must not be reachable from the network. The repository adds no firewall rule for this `0.0.0.0` listening: the `pumba-expose-*` rules exist only in `expose.py`.

### `expose` relay (shipped code, absent from the guides)

`scripts/host/deploy/expose.py`, module docstring:

> The containers stay on 127.0.0.1. This process listens on one LAN address and copies a connection to that same port on 127.0.0.1 only when the source address is on the list.

`DOORS` is `(8080, model, /v1)`, `(8090, page, /ui)`, `(8092, mcp, /mcp)`, `(3000, grafana, /)`. `RULE_PREFIX = "pumba-expose"`. `cmd_expose` is wired in `scripts/host/deploy/cli.py` (subcommand `expose`, `set_defaults(func=d.cmd_expose)`). `scripts/host/deploy/__init__.py` imports `cmd_expose`. `scripts/host/deploy/runtime.py`, `_needs_container_engine`, does not start the container engine for the commands of `_HOST_ONLY_COMMANDS`, which include `expose`. The relay assumes that the stack already listens on `127.0.0.1`; `up` does not start `expose`.

Behavior read in `cmd_expose` and `serve`:

- Without any IPv4 address (`--allow` or `state/expose-allow.txt`), the process stops and does not open the ports (`cmd_expose`).
- `parse_allowlist` refuses `*`, `0.0.0.0`, `0.0.0.0/0`, any entry containing `/`, a non-IPv4 address, and `0.0.0.0` / multicast / broadcast (`parse_allowlist`). Several distinct addresses are kept.
- The listening is a single LAN IPv4 address (`--bind`, otherwise the address of the default route). A loopback address as the default route is refused (`default_bind_address`).
- Each accepted connection whose source IP is on the list is copied to `127.0.0.1` on the same port number, in a thread (`serve`, `_bridge`). A source absent from the list is closed.
- `listen(16)` is the accept backlog, not a cap on workstations.
- On Windows, an inbound rule `pumba-expose-<port>` limits `RemoteAddress` to the listed IPv4 addresses (`firewall_allow_command`). Elsewhere, `apply_firewall` adds no rule and the relay filters the addresses anyway.
- Ctrl+C closes the sockets and removes the rules. `--down` removes the rules and exits, without leaving the relay running.
- If `bind` fails (`OSError`), that port is reported as "not forwarded" and the other ports continue (`cmd_expose`). On `evo-x5`, `llama-server` already holds `0.0.0.0:8080` (`serve_argv`): the relay cannot in turn bind the LAN address on 8080. The model stays on the `0.0.0.0` listening of the host process; the page and the MCP are not on that listening.

`tests/scripts/test_expose.py` covers the list (two addresses, rejection of `0.0.0.0` and of a network), the text of the firewall rule for `192.168.1.40,10.0.0.2`, the relaying of an allowed peer, and the closing of an absent peer. There is no test in that file with two real machines against the stack.

No guide under `docs/` names `expose`, `expose-allow.txt`, or `pumba-expose`. `README.md`, section "Quick start", lists the commands of `--help` without `expose`, whereas the usage text of `scripts/host/deploy/cli.py` contains `python scripts/deploy.py expose --allow 192.168.1.40`.

`ui-proxy` bounds the page's connections: `worker_processes 1` and `worker_connections 64` (`containers/host-proxy/nginx.conf`). The MCP does not go through this proxy.

## Authentication

A single secret for the whole agent API. `SECURITY.md`, section "Authentication":

> `agent`'s `/chat` requires a static bearer token (`AGENT_BEARER_TOKEN` […]).

The same section says that the MCP is published on loopback and forwards `ask` to `/chat` with this token. `SECURITY.md`, "Known limitations":

> No rate limiting or per-user authorization, one shared bearer token for the whole `agent` API.

`docs/architecture/security.md`, "What this does *not* claim to defend against", says that this is a local development tool, not a multi-tenant production service, and that there is no per-user authorization. `docs/notes/verification/stack-presentation.md`, "4.2 Agent side", says the same: a single shared token, no per-user quotas, the semaphore only bounds global concurrency.

In the code:

- `services/agent/main.py`, `verify_bearer_token`, requires `Authorization: Bearer <AGENT_BEARER_TOKEN>` (comparison with `secrets.compare_digest`). An empty, missing, or wrong token gives 401. The routes `/lookup`, `/read`, `/check`, `/lsp/outline`, `/lsp/calls`, `/lsp/types`, `/models`, `/models/select`, `/chat`, `/review`, `/chat/cancel`, and `/chat/stream` declare this dependency.
- `GET /ui` does not have this dependency. The docstring of `ui_page` says that the page has no token: it is entered in the page and sent only on `/chat/stream`. `services/agent/ui/index.html` stores it in `sessionStorage` for the tab and sends it as `Authorization: Bearer` on `/models`, `/models/select`, and the chat stream.
- The HTTP MCP has no token check on the client side. `services/mcp/main.py`, `WorkspaceHeader`, only copies `X-Workspace`. `services/mcp/agent_client.py`, `_bearer`, reads `AGENT_BEARER_TOKEN` in the container and adds it to the calls to `agent`. `docs/notes/contracts/mcp.md`, "From the host", says that the token stays in the `mcp` container and that the editor does not send it.
- The model on 8080 is not this token. `containers/llm/entrypoint.sh` does not pass an API key to `llama-server`. `docs/guides/vscode.md`, Continue.dev section, says that `apiKey` only has to be non-empty (`apiKey: local` in the example).
- `python scripts/deploy.py rotate-token` regenerates the token and recreates `agent`, `app`, and `mcp` (`scripts/host/deploy/cli.py`, help of `rotate-token`). It is a replacement of the shared secret, not a per-workstation secret.

So: once the page, the MCP, or port 8080 is reachable, all the authorized workstations share the same power. The MCP and port 8080 do not require the agent token. The chat page requires it, and any workstation that has it can send it.

## One remote client, then several

### One workstation, written procedure

`docs/guides/vscode.md`, "Before opening the editor" (translated from French):

> If the editor is on a different machine from the one serving the model, open an SSH tunnel from the workstation where VS Code runs:
>
> `ssh -L 8090:127.0.0.1:8090 -L 8092:127.0.0.1:8092 -L 8080:127.0.0.1:8080 user@server-machine`
>
> All the addresses below then stay `127.0.0.1`.

This tunnel covers the page, the MCP, and the model. The Continue, VS Code, and Cursor examples on this page, and those of `docs/notes/contracts/mcp.md`, use `http://127.0.0.1:8092/mcp` and `http://127.0.0.1:8090/ui`. `scripts/host/workspaces.py`, `install_vscode_mcp`, writes the same URL `http://127.0.0.1:8092/mcp` into `.vscode/mcp.json`. With the tunnel, this URL stays valid on the workstation. The guide shows one command, for the workstation where the editor runs. It does not describe several workstations, an account per person, or a service that would stay open without an SSH session.

`docs/guides/evo-x5.md`, "VS Code", says "On the same machine" (translated from French) and gives `127.0.0.1`. The case of an editor elsewhere is in `docs/guides/vscode.md`, not in the EVO-X5 guide.

### Several workstations, what is in the code

`expose` is the only mechanism of the repository that names several machines. `--allow` is repeatable (`cli.py`). `parse_allowlist` accumulates several IPv4 addresses. `test_parse_allowlist_keeps_addresses_and_comments` expects `192.168.1.40` and `10.0.0.2`. Each accepted connection has its own thread (`serve`). Nothing in `expose.py` identifies a user: the filter is the source IPv4 address.

Once the connection is established (SSH tunnel from that workstation, or relay if its IPv4 address is listed), the requests are those of the API:

- Page: `GET /ui`, then `POST /chat/stream` with the token (`ui_page`, `index.html`).
- API: `POST /chat` with the token (`CONTRACTS.md`, "agent: POST /chat"; `verify_bearer_token`).
- MCP: `POST /mcp`, tools of `services/mcp/server.py`. `stateless_http=True` on the `FastMCP`: no MCP session kept on the server side. `ask` calls `POST /chat`. The other tools call `/lookup`, `/check`, or `/lsp/*`, which have the token on the container side but do not take the `_CHAT_SLOTS` slot.

Caps, the same whether there is one workstation or several:

- `AGENT_MAX_CONCURRENT`, default 2 (`compose.yaml`, service `agent`; `CONTRACTS.md`). `services/agent/main.py`, `_SlotGate`, refuses anything beyond with HTTP 503, detail `too many concurrent /chat requests`, without a queue. `/chat` and `/chat/stream` go through `_claim_turn`. `/review` takes the same semaphore. `/lookup` and `/check` do not take it. `scripts/host/functional_check.py`, step "24. Two lookups at once", comments that this case targets the RAG path, not the `/chat` cap.
- Two requests with the same `conversation_id` wait for each other: `_claim_turn` says that the same id does not receive 409, and that a distinct conversation receives 503 when all the slots are taken. Without `conversation_id`, `chat` makes one up (`uuid.uuid4()`). The history is in memory, with a TTL, without disk (`CONTRACTS.md`, "agent: POST /chat"). There is no user key.
- `LLM_PARALLEL=1` (`CONTRACTS.md`: "llama-server --parallel; one slot so the KV cache stays small"). `containers/llm/entrypoint.sh` passes `--parallel`. `LLM_MODELS_MAX=1` (`compose.yaml`). The repository does not describe what `llama-server` answers to a second completion while this single slot is busy. The agent's semaphore, for its part, lets two through.
- `tools` has cgroup limits `cpus: 2`, `memory: 4G`, `pids_limit: 256` (`compose.yaml`, service `tools`). `SECURITY.md` says that concurrent `/build` and `/test` requests do not share a build directory.

## Workspace and code checkout

The queried checkout is a directory on the machine where the stack runs.

`scripts/host/deploy/stack.py`, `cmd_workspace`: if the path does not exist on that machine, the command stops with "Pass a directory that exists on this machine." It registers the remote repository (git URL or Subversion root), writes `.vscode/mcp.json` into that checkout, and mounts each registered checkout.

`scripts/host/workspaces.py`, `compose_mounts_text`: each entry of `state/workspaces.json` becomes a volume. `tools` and `agent` read-only, `patch` and `serena` read-write. Several checkouts are planned for: the module docstring says that a second ticket of the same remote registers a path and a mount, without starting over on a fresh index.

`services/agent/checkouts.py`, `resolve_workspace`: the path is looked up in the registry. Otherwise `UnknownWorkspace`. `services/agent/main.py`, `_checkout_or_400` and the `chat` handler, translate this into HTTP 400, detail `workspace is not registered`. `CONTRACTS.md`, Qdrant variables: "Unregistered paths are refused." `docs/guides/vscode.md` says that `X-Workspace` is the path of the checkout on the machine where the stack runs.

Consequence for a separate workstation: the header must carry this server path, not a folder that exists only on the laptop. The uncommitted files re-read in front of the index are those of this server checkout (`docs/guides/using-the-assistant.md`, "A repository of thousands of files"; `services/agent/checkouts.py`, `parse_status_paths`). The editor, on the workstation, applies its own diffs: `docs/guides/vscode.md`, "Who modifies the files" (translated from French), says that in VS Code the `pumba` MCP exposes neither `edit` nor `apply`. The same passage says that on the page the agent goes through `patch` (`edit` then `apply`); this is outdated as of 2026-11-12: `begin_plan`, `edit`, and `apply` are no longer named tools, and writing on the page goes through the Serena named write tools (`rename_symbol`, `replace_*`, `insert_*`, `delete_lines`, `create_text_file`, `safe_delete_symbol`), on the stack's machine, without a commit (`services/agent/serena_named_tools.py`).

There is, neither in `cmd_workspace` nor in the `workspace` API, any upload of the workstation's disk to the server.

## Written procedure

To start the stack, including on a Linux host: `docs/getting-started/prerequisites.md` and `docs/getting-started/installation.md`. The command is `python scripts/deploy.py up`. Python 3.9+ on that machine is enough for the script. Podman or Docker Compose runs the containers. `installation.md` indicates the `evo-x5` profile before `up` on the GMKtec machine. Nothing in these two pages talks about a client workstation.

To use it: `docs/getting-started/first-run.md` opens `http://127.0.0.1:8090/ui` and has the reader paste `AGENT_BEARER_TOKEN`. `docs/guides/using-the-assistant.md` registers a checkout with `python scripts/deploy.py workspace <path>`, then opens that folder in VS Code. `docs/guides/evo-x5.md` repeats the page, the token, and the MCP on `127.0.0.1` on the same machine. `docs/guides/vscode.md` is the only guide that describes an editor on another machine, through the SSH tunnel cited above.

`README.md`, "Quick start", gives `up`, `ask`, and the MCP URL `http://127.0.0.1:8092/mcp`.

## Gaps

- The guides and `SECURITY.md` describe loopback ports and say not to publish `0.0.0.0`. They do not describe `expose`. The relay is shipped in the code and in the allowlist tests, not in the user guide.
- `expose` is a foreground process. Ctrl+C closes it and removes the firewall rules it added. `up` does not install it as a service.
- `install_vscode_mcp` always writes `http://127.0.0.1:8092/mcp`. A workstation that goes through the LAN relay must change this URL; the repository does not write it.
- No account, no per-workstation quota, one shared token (`SECURITY.md`). The MCP and port 8080, once reachable, do not ask the client for this token.
- At most two simultaneous `/chat` by default. Beyond that, 503. A single `llama-server` slot (`LLM_PARALLEL=1`) and a single chat model loaded.
- The workstation's code is not brought over. `check`, the index, `patch`, and `serena` see the checkouts mounted on the server.
- On `evo-x5`, the model listens on `0.0.0.0:8080` without the relay's address list. The page and the MCP stay on loopback as long as the relay or a tunnel does not open them. The EVO-X5 guide asks not to forward 8080 on the router.
- `docs/guides/migration-mcp-externe.md` is a migration guide, not the current behavior. It says, about `state/workspaces.json` and the `/checkouts/<id>` mounts, that a remote workstation can only run `check` if the GPU machine sees its folder, or if `check` is limited to the checkouts present on that machine. The current code is already the second case: `cmd_workspace` refuses a missing folder, and `resolve_workspace` refuses an unregistered path.

## Sources

- `README.md`
- `SECURITY.md`
- `CONTRACTS.md`
- `compose.yaml`
- `config/compose/host-llm.yaml`
- `config/compose/observability.yaml`
- `containers/llm/entrypoint.sh`
- `containers/host-proxy/nginx.conf`
- `docs/index.md`
- `docs/architecture/overview.md`
- `docs/architecture/networking.md`
- `docs/architecture/security.md`
- `docs/notes/verification/stack-presentation.md`
- `docs/getting-started/prerequisites.md`
- `docs/getting-started/installation.md`
- `docs/getting-started/first-run.md`
- `docs/guides/evo-x5.md`
- `docs/guides/vscode.md`
- `docs/guides/using-the-assistant.md`
- `docs/guides/observability.md`
- `docs/guides/migration-mcp-externe.md` (migration, cited separately from the current code)
- `docs/notes/contracts/mcp.md`
- `scripts/host/deploy/__init__.py`
- `scripts/host/deploy/cli.py`
- `scripts/host/deploy/expose.py`
- `scripts/host/deploy/runtime.py`
- `scripts/host/deploy/stack.py`
- `scripts/host/evo_x5_serve.py`
- `scripts/host/functional_check.py`
- `scripts/host/workspaces.py`
- `services/agent/main.py`
- `services/agent/checkouts.py`
- `services/agent/ui/index.html`
- `services/mcp/main.py`
- `services/mcp/server.py`
- `services/mcp/agent_client.py`
- `tests/scripts/test_expose.py`
