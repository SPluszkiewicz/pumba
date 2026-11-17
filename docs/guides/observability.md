# Observability

Prometheus collects the metrics. Grafana displays them on the machine, at
`http://127.0.0.1:3000`. None of this is published outside the
host. The `obs` profile is not started by `deploy.py up`.

Two dashboards:

| Dashboard | Question |
|---|---|
| Pumba | Are the services responding, at what HTTP throughput, with what latency and what memory? |
| `Pumba analyse` (Pumba analysis) | Is a turn slow because of the model, the tools, the search or the context window? |

## Getting started

The password is `GRAFANA_ADMIN_PASSWORD` in `.env`. Grafana reads it
only once, when the `grafana-data` volume is created. Changing it in
`.env` does not change an already initialized volume. The default user
is `admin` (`GRAFANA_ADMIN_USER`).

The new business series are in the `agent` image. The embedder and reranker
series require `--metrics`, hence a rebuild of those two images. Prometheus
and Grafana read their configuration from the repository: recreating them is
enough.

```bash
podman compose build agent embedder reranker
podman compose -f compose.yaml -f config/compose/podman.yaml -f state/compose.workspaces.yaml -f config/compose/observability.yaml --profile obs up -d prometheus grafana
```

With Docker, keep the same files as the rest of the stack, including
`config/compose/docker.yaml` (in place of `podman.yaml`):

```bash
docker compose -f compose.yaml -f config/compose/docker.yaml -f state/compose.workspaces.yaml -f config/compose/observability.yaml --profile obs up -d prometheus grafana
```

These are the files that `deploy.py` stacks itself
(`config/compose/podman.yaml` for Podman, `docker.yaml` for Docker).
`state/compose.workspaces.yaml` only exists after a first
`python scripts/deploy.py workspace <path>`: without that file, remove the
`-f state/compose.workspaces.yaml`. To stop them without touching the rest:
same command with `stop prometheus grafana` in place of `up -d prometheus
grafana`. Their volumes (`prometheus-data`, `grafana-data`) remain.

Open `http://127.0.0.1:3000`. The home dashboard is **Pumba**. The
**`Analyse`** (Analysis) link at the top opens the other one. Both reload
on their own.

Prometheus has no port on the host. It is attached to `core-net` and
`tools-net` to read `/metrics`, and to `obs-net` for Grafana. Grafana
is only on `obs-net`.

Samples are kept for 30 days.

## Reading a panel

The rates on the analysis dashboard are computed over **15 minutes**. Right after a
restart, or if no question has been asked, a business panel stays
empty: this is not a failure. **No data** on `up` or on an HTTP throughput,
on the other hand, means the target is not being scraped.

`increase(...[$__range])` follows the period chosen at the top right
(6 h on Pumba, 24 h on the analysis dashboard). Zooming changes this figure.

The percentiles (`p50`, `p95`) come from histograms. They describe the
turns in the window, not the last turn alone.

## Pumba

| Panel | Reading |
|---|---|
| `Agent`, `Tools`, `Modèle` (Model) | `1`: Prometheus reaches `/metrics`. `0`: the container is stopped, or the scraped model name no longer matches (see below). |
| `Requêtes par service` (Requests per service), `Routes de l'agent` (Agent routes), `Routes de tools` (Tools routes) | HTTP throughput. `/metrics` itself counts in the total. |
| `Latence p95` (p95 latency) | HTTP response time. A long chat turn raises the agent's latency, because `/chat` only responds at the end of the turn. |
| `Mémoire résidente` (Resident memory) | RSS of the `agent` and `tools` Python processes. |
| `Appels d'outils` (Tool calls) | Throughput per name, with `ok=true` or `ok=false`. |
| `Recherche dans l'index` (Index search) | `code` gate only. `contexte=false` (context=false): embedding failed or no chunk retained. |
| `Appels malformés (fenêtre)` (Malformed calls (window)) | Tool calls rejected by validation before any HTTP. |
| `Débit (tokens/s, dernier scrape)` (Throughput (tokens/s, last scrape)) | Last llama.cpp measurement (tokens/s), not an average. |
| `Requêtes du modèle` (Model requests) | Requests in progress and pending in llama-server. |

## Pumba analysis

| Area | What it lets you decide |
|---|---|
| `Tours par porte` (Turns per gate) | Share of `direct` questions (no index, no tools) and `code` questions. |
| `Issue des tours` (Turn outcome) | `ok`, `cancelled`, `llm_unreachable`, `context_overflow`, `malformed_giveup`, `repeated_tool`, `max_iterations`, `error`. A rise in `max_iterations` means the loop reached `AGENT_TOOL_LOOP_MAX_ITERATIONS` without a final answer. `repeated_tool`: the model called the same tool again with the same arguments. |
| `Durée d'un tour` (Turn duration) | Wall time, from classification to the optional review. |
| `Itérations p95` (Iterations p95) | LLM turns in the loop. The text-only fallback is not counted. A `cancelled` turn stays at 0: the abort does not return the count. |
| `Outils par tour p95` (Tools per turn p95) | How many executions a turn launches. |
| `Places de chat` (Chat slots) | Turns holding a slot, and the `AGENT_MAX_CONCURRENT` cap. |
| `Chats refusés (fenêtre)` (Rejected chats (window)) | Requests returned as HTTP 503 because the cap was reached. |
| `Jetons du prompt après budget` (Prompt tokens after budget) | Size measured after the budget pass, at each iteration. |
| `Remplissage de la fenêtre p95` (Window fill p95) | Share of the window occupied. Close to 100%, the following turns compress or overflow. |
| `Budget : compressions et dépassements` (Budget: compressions and overflows) | The budget shortened the prompt, or llama-server refused the context. |
| `Durée de la recherche` (Search duration) | Embedding, Qdrant search, optional rerank. The checkout overlay is outside this timer. |
| `Contexte trouvé et reranker` (Context found and reranker) | Chunks retained or not, and whether the reranker provided an order. |
| `Extraits cités p95` (Cited chunks p95) | Number of chunks given to the model. |
| `Taux de succès par outil` (Success rate per tool) | Share of executions whose response has `ok: true`. A tool blocked by the editing policy counts as a failure. |
| `Durée p95 par outil` (p95 duration per tool) | Wall time, including a blocked call. A long `build` shows up here, not in the HTTP latency of `tools` alone if you compare tools with each other. |
| `Phrases retirées par la relecture` (Sentences removed by the review) | Sentences removed by the review (`AGENT_REFLECT=1`). A review that changes nothing, or a disabled review, leaves the counter at zero. |
| `HTTP 5xx` | Server errors from `agent` and `tools`. |
| `Débit de génération (jetons/s, 15 min)` (Generation throughput (tokens/s, 15 min)) / `Débit de prompt (jetons/s, 15 min)` (Prompt throughput (tokens/s, 15 min)) | Tokens per second **averaged over 15 minutes**, from the llama.cpp counters. More stable than the gauge on the Pumba dashboard, which is the last call. |
| `Instantanés llama.cpp` (llama.cpp snapshots) | Generation and prompt throughput of the last scrape, and the largest `n_tokens` seen. |
| `File du modèle de chat` (Chat model queue) | Requests in progress, pending, and slots busy with `llama_decode`. |
| `Débit embedder et reranker (jetons/s, 15 min)` (Embedder and reranker throughput (tokens/s, 15 min)) | Same prompt throughput, for question indexing and rerank. |
| `Collections`, `Points`, `Vecteurs` (Vectors), `Mémoire Qdrant` (Qdrant memory) | Qdrant state. `collection_points` is an approximation per collection, summed. |

`route=unknown` on a turn means that the classification itself
failed before choosing `direct` or `code`.

## Series exposed by the agent

In addition to the HTTP series from `prometheus-fastapi-instrumentator`
(`http_requests_total`, `http_request_duration_highr_seconds_bucket`,
process memory):

| Series | Type | Labels |
|---|---|---|
| `tool_calls_total` | counter | `name`, `ok` |
| `tool_call_duration_seconds` | histogram | `name` |
| `tool_call_malformed_total` | counter | |
| `rag_used_context_total` | counter | `used` |
| `rag_retrieval_duration_seconds` | histogram | |
| `rag_sources` | histogram | |
| `rag_reranker_total` | counter | `used` |
| `agent_turns_total` | counter | `route`, `outcome` |
| `agent_turn_duration_seconds` | histogram | `route`, `outcome` |
| `agent_turn_iterations` | histogram | |
| `agent_turn_tool_calls` | histogram | |
| `agent_context_tokens` | histogram | |
| `agent_context_window_ratio` | histogram | |
| `agent_context_compressed_total` | counter | |
| `agent_context_overflow_total` | counter | |
| `agent_reflection_sentences_removed_total` | counter | |
| `agent_chat_slots_in_use` | gauge | |
| `agent_chat_slots_limit` | gauge | |
| `agent_chat_rejected_total` | counter | |

The `/metrics` of the agent and of `tools` is not authenticated. It is only reachable
on the internal networks, and through `ui-proxy` for the functional
check. Do not publish it.

## Scraped targets

| Job | Internal URL |
|---|---|
| `agent` | `http://agent:8090/metrics` |
| `tools` | `http://tools:8091/metrics` |
| `llm` | `http://llm:8080/metrics?model=<stem>` |
| `embedder` | `http://embedder:8081/metrics` |
| `reranker` | `http://reranker:8082/metrics` |
| `qdrant` | `http://qdrant:6333/metrics` |

llama.cpp (build `b10216`) publishes the counters
`llamacpp:prompt_tokens_total`, `llamacpp:prompt_seconds_total`,
`llamacpp:tokens_predicted_total`, `llamacpp:tokens_predicted_seconds_total`,
and the gauges `llamacpp:prompt_tokens_seconds`,
`llamacpp:predicted_tokens_seconds`, `llamacpp:requests_processing`,
`llamacpp:requests_deferred`, `llamacpp:n_busy_slots_per_decode`,
`llamacpp:n_tokens_max`. It does not expose KV cache occupancy.

`mcp`, `ui-proxy`, `serena` and the one-shot indexer are not scraped.

!!! warning "`evo-x5` profile (model on the host)"
    In this mode, the `llm` container does not run: the `llm` target
    (`llm:8080`) stays failed and Prometheus does not scrape the host's
    `llama-server`. The `Modèle` (Model) panel stays at `0`, and the throughput and
    model queue panels are empty. `agent`, `tools`, `embedder`, `reranker` and
    `qdrant` are scraped as usual.

## Changing the chat model

At startup, Prometheus replaces `__LLM_METRICS_MODEL__` with the file name
of `LLM_MODEL_PATH` without `.gguf`. The characters allowed in this name:
letters, digits, dot, `_` and `-`.

Loading another GGUF from the page does not update this scrape. The
`Modèle` (Model) panel goes to unreachable until `LLM_MODEL_PATH` points to
the loaded file, and then `prometheus` must be recreated. The throughput on the
analysis dashboard concerns the scraped model, not a model that has only been
hot-loaded.

## Troubleshooting

- **Grafana asks for a password that `.env` no longer contains.** The volume
  kept the first password. Restore that value, or delete the
  `grafana-data` volume (local Grafana accounts are lost, the provisioned
  dashboards come back at startup).
- **Model unreachable, agent and tools green.** The scraped stem does not
  match the loaded model, or `llm` does not have `--metrics`.
- **Embedder or reranker missing from Prometheus.** Rebuild the images
  after adding `--metrics`, then recreate them.
- **Qdrant panels empty while `up{job="qdrant"}` is 1.** The series
  name changed with the image. The names used here are those of Qdrant
  1.19: `collections_total`, `collection_points`, `collections_vector_total`,
  `memory_resident_bytes`.
- **Percentiles stay empty after questions.** Wait for at least
  a 15-minute window with traffic, or widen the displayed period.
