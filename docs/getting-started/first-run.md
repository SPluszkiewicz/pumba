# First run

What each box is for, without the jargon:
[Plain-language overview](../concepts/en-clair.md).

The page you use is on the host, through `ui-proxy`:

`http://127.0.0.1:8090/ui` (`AGENT_PORT` in `.env`)

`agent` itself is not published. If the browser says the connection was
refused, the proxy is stopped. Start it with the rest of the stack:

```bash
python scripts/deploy.py up
```

`up` already indexed `CODEBASE_PATH` (default `./sample`). If its last step
failed, the stack is up but empty: fix the error, then
`python scripts/deploy.py index`. When something else is wrong,
`python scripts/deploy.py status` lists the containers and
`python scripts/deploy.py doctor` explains what it finds
([README](../../README.md#doctor)).

## Ask a question

1. Paste `AGENT_BEARER_TOKEN` from `.env` into `Jeton` (Token) and press
   `Mémoriser` (Remember). The token stays in this browser tab only.
2. Type a question and press `Envoyer` (Send).
3. The trace shows the route (`direct` or through the index and tools),
   then tokens as the model writes them, then each tool call.

From a terminal, `python scripts/deploy.py ask "..."` (one question) or
`python scripts/deploy.py chat` (multi-turn) do the same through the `app`
container.

A direct question (a greeting, a bit of arithmetic) does not search the
repository. A question about code retrieves a few chunks, then may call
named tools. `agent` and `tools` mount the checkout read-only. A change goes
through a write tool of the `serena` sidecar (`replace_symbol_body`,
`replace_content`, `create_text_file`, ...), which writes the checkout. That
puts the turn in **editing**: `build`, `test`, and `compile_file` are refused
until one `build_step` succeeds. There is no commit and no push.

## Context meter

Above the question, `Contexte` (Context) shows how full the window is
(`used / window` tokens). The window is `LLM_CTX_SIZE`: 32768 on the
14B profiles, 262144 on `evo-x5`. When a follow-up would overflow, older turns are
shortened into one note. The last question is kept. The trace says when
that happens.

## Choose a model

`Modèle de chat` (Chat model) lists the chat GGUF files in `./models/`. Embedder and
reranker files are hidden. Press `Charger` (Load). The page calls `agent`
(`POST /models/select`), which asks `llm` to unload the current model and
load the one you picked. Only one chat model is in
memory (`LLM_MODELS_MAX=1`). The first load of the 14B takes about half
a minute. `LLM_MODEL_PATH` is what comes back after a recreate of `llm`,
not what you last clicked. On `evo-x5` the chat model is the host
process, alias `mistral-small-4`. The page does not swap it.

Details: [Using the assistant](../guides/using-the-assistant.md) and
[Swapping the model](../guides/swapping-the-model.md).
