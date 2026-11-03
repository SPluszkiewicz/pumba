# Testing

Every test in this project runs **inside a container** - `devtools` for
everything except the end-to-end smoke test, which has its own dedicated
`test` compose profile. Nothing here needs anything installed on the host
beyond Podman.

## Per-service Python unit tests

Each Python service (`indexer`, `agent`, `tools`, `mcp`) has its own test suite
under `tests/<service>/`, plus `tests/scripts/` for the setup scripts. All of
them are pure unit tests - no real network calls, no real Qdrant/llama.cpp
processes - so they run in a couple of seconds each, with every outbound
HTTP call mocked/stubbed.

GitHub Actions job `unit` (`.github/workflows/ci.yml`) runs the same pytest
suites on every pull request: `python -m pytest -q --ignore=tests/integration`.
It installs `pytest-asyncio` because `pytest.ini` sets `asyncio_mode = auto`.
The RTX 6000 profile campaign is not part of that job.

Run all of them at once:

```bash
podman compose run --rm devtools pytest tests/ -v
```

Or just one service while you're actively working on it:

```bash
podman compose run --rm devtools pytest tests/tools -v
podman compose run --rm devtools pytest tests/agent -v
podman compose run --rm devtools pytest tests/indexer -v
podman compose run --rm devtools pytest tests/mcp -v
```

A live checkout is a different check. `python scripts/deploy.py eval`
calls `POST /lookup` on a registered tree and reports whether the expected
file is in the final 5, only in the prefetch, or absent. It does not call
the chat model. See [Retrieval baseline](eval-checkout.md).

The `tools` service's tests deserve special attention if you're touching
that code - `tests/tools/test_path_validation.py` is the most
security-critical suite in the repo (path traversal, symlink escapes,
Windows drive-anchor smuggling, null bytes). If you add a new path-taking
endpoint, add rejection tests for every category in that file before
anything else.

## C++ unit tests (ctest)

Tests are Catch2 v3 (`TEST_CASE` / `REQUIRE`). CMake's
`catch_discover_tests` registers each case as its own ctest entry, so
`ctest -R json_round_trip` (or `-R request_url`) runs a single case. The
framework comes from Ubuntu 24.04's `catch2` apt package - not FetchContent
- because the `tools` container configures this tree on an isolated network.

```bash
podman compose run --rm devtools bash -c \
  "cd app && cmake --preset release && cmake --build --preset release && ctest --preset release --output-on-failure"
```

Or use the `debug` preset instead of `release` for a build with symbols
while iterating.

`sample/` is a separate CMake tree (the calculator fixture, not the
client). Its `ctest --preset release` hides the `known_bug*` cases on
purpose. See [Review fixture](review-fixture.md).

Interactive multi-turn (streams tokens, keeps `conversation_id`):

```bash
python scripts/deploy.py chat
# or: podman compose run --rm -it app --repl
```

One-shot still uses SSE by default (`llm-client "<question>"`). `--no-stream`
hits `POST /chat` for scripts.

## RTX 6000 profile campaign

`python scripts/host/profile_campaign.py` prints the profiles that match
`nvidia-smi` (`24g` and `24g-30b`, or `48g`, `48g-30b` and `48g-32b`).
`--run --workspace <sample>` applies them and writes
`state/profile-campaign.json`. Questions and pass rules are in
[Scénarios RTX 6000](rtx6000-scenarios.md). GitHub Actions does not run it.

## EVO-X5 agent comparison

`python scripts/host/agent_compare.py plan` prints the matrix for the
GMKtec EVO-X5 Pro. `prepare`, `score`, and `decide` are in
[Comparaison d'agents](evo-x5-agent-compare.md). GitHub Actions does not
run the campaign. The unit tests cover the scorer only.

## Tool-calling evaluation (`scripts/container/eval_tools.py`)

Scores whether the agent's **first** tool call matches a fixture
(`question`, `expected_tool`, `expected_args_subset`) - no LLM-as-judge.
Requires a running stack and `AGENT_BEARER_TOKEN`:

```bash
podman compose run --rm devtools python scripts/container/eval_tools.py
```

Scenarios live in `scripts/container/eval_tools_scenarios.json`. Add one when you
change a tool name, a default argument, or the system prompt's "verify
before asserting" wording. Nightly CI can run this after `eval_rag.py`.

## RAG retrieval evaluation (`scripts/container/eval_rag.py`)

A golden-question check that the *right chunks get retrieved* - not an
exact-match check on the LLM's free-text answer (which isn't byte-stable).
Requires a running, indexed stack:

```bash
podman compose up -d qdrant llm embedder tools agent mcp
podman compose --profile index run --rm indexer
podman compose run --rm devtools python scripts/container/eval_rag.py
```

Add new golden questions to `scripts/container/eval_rag_questions.json` as the
codebase under `app/` grows - each entry's `expected_file_substring` is
matched against the `file_path` field of the agent's returned `sources`
(which, per `CONTRACTS.md`, is relative to `app/` with no `app/` prefix -
e.g. `src/main.cpp`, not `app/src/main.cpp`).

## End-to-end smoke test (`test` compose profile)

Or run `python scripts/deploy.py test` to do all of the below in one command
(including teardown, even on failure).

Brings up the full stack with a **tiny, CPU-only chat model** swapped in for
`llm` (the embedder is never swapped - see `CONTRACTS.md`) so this runs
quickly without a GPU, then runs `tests/integration/test_smoke.py` against
it from inside the `smoke-test` service (built from the same `devtools`
image, attached to every internal network so it can probe each service
directly, not just through `agent`).

```bash
# One-time: fetch the tiny CI model (and the real embedder model, which the
# test profile still needs - see CONTRACTS.md). `run` with an explicit
# command overrides the service's default one, so pass the --test flag this
# way rather than appending it.
podman compose --env-file .env.test --profile setup run --rm fetch-models \
  python scripts/container/fetch_models.py --test

# Build the CPU-only `llm` image and bring the stack up.
podman compose --env-file .env.test build llm embedder tools agent mcp
podman compose --env-file .env.test up -d qdrant llm embedder tools agent mcp

# Run the smoke test.
podman compose --env-file .env.test --profile test run --rm smoke-test

# Tear down.
podman compose --env-file .env.test down
```

What it checks (see the module docstring in `tests/integration/test_smoke.py`
for the full rationale):

- every service's health endpoint responds within a generous startup window
- `agent`'s bearer-token auth actually rejects missing/wrong tokens
- a basic `/chat` round-trip works and degrades correctly with **no**
  indexed context (this stack's Qdrant collection starts empty)
- a tool-call round-trip completes without erroring (whether the tiny model
  *chooses* to call a tool is logged but not asserted - small models are
  known to be unreliable at structured tool-calling; that's precisely why
  `CONTRACTS.md` mandates a 7B+ model at Q5_K_M for production)

This is the profile CI should run on every change - it's the only test that
actually exercises the real network segmentation, healthcheck ordering, and
inter-service wiring rather than a mocked version of it.
