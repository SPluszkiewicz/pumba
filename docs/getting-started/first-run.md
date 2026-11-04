# First run

What each box is for, without the jargon:
[Comprendre sans bagage](../concepts/en-clair.md).

The page you use is on the host, through `ui-proxy`:

`http://127.0.0.1:8090/ui`

`agent` itself is not published. If the browser says the connection was
refused, the proxy is stopped. Start it with the rest of the stack:

```bash
python scripts/deploy.py up
```

or, when the other services are already healthy:

```bash
docker compose -f compose.yaml -f config/compose/docker.yaml up -d ui-proxy
```

## Ask a question

1. Paste `AGENT_BEARER_TOKEN` from `.env` into **Jeton** and press
   **Mémoriser**. The token stays in this browser tab only.
2. Type a question and press **Envoyer**.
3. The trace shows the route (`direct` or through the index and tools),
   then tokens as the model writes them, then each tool call.

A direct question (a greeting, a bit of arithmetic) does not search the
repository. A question about code retrieves a few chunks, then may call
tools. `tools` and the agent's mount stay read-only. A change goes
through `patch`: `edit` proposes a diff, `apply` writes that copy. There
is no commit and no push.

## Context meter

Above the question, **Contexte** shows how full the window is
(`used / window` tokens). The window is `LLM_CTX_SIZE`: 32768 on the
14B profiles, 262144 on `evo-x5`. When a follow-up would overflow, older turns are
shortened into one note. The last question is kept. The trace says when
that happens.

## Choose a model

**Modèle de chat** lists the chat GGUF files in `./models/`. Embedder and
reranker files are hidden. Press **Charger**. The server unloads the
current model and loads the one you picked. Only one chat model is in
memory (`LLM_MODELS_MAX=1`). The first load of the 14B takes about half
a minute. `LLM_MODEL_PATH` is what comes back after a recreate of `llm`,
not what you last clicked. On `evo-x5` the chat model is the host
process, alias `mistral-small-4`. The page does not swap it.

Details: [Using the assistant](../guides/using-the-assistant.md) and
[Swapping the model](../guides/swapping-the-model.md).
