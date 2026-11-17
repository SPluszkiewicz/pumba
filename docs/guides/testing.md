# Testing

The default way to run the tests is **inside a container**: the `devtools`
service (profile `dev`) for everything except the end-to-end smoke test,
which has its own `test` compose profile. That needs nothing on the host
beyond Podman. `devtools` carries pytest and the C++ toolchain, but not
`ruff` or `mypy`: those two only run in CI (and in the optional
`.pre-commit-config.yaml` hooks). A host virtualenv works too for the Python
suites (see below).

## Python unit tests

`tests/` has one directory per component: `agent`, `common`, `indexer`,
`mcp`, `tools`, `trainer`, plus `scripts` (the `deploy` CLI, the
host and container scripts, compose hardening, observability config,
`CONTRACTS.md` sync). `tests/integration`
is the container smoke test (see below), not a unit suite. The unit suites
are pure: no real network calls, no real Qdrant or llama.cpp processes, with
every outbound HTTP call mocked or stubbed. They run in a couple of seconds
each.

GitHub Actions job `unit` (`.github/workflows/ci.yml`) runs on every pull
request, on a plain Ubuntu 24.04 runner with Python 3.11 and no containers:
`ruff check .`, `mypy` per service directory, `python -m pytest -q
--ignore=tests/integration`, `clang-format --dry-run` on `app/` and
`sample/`, and the C++ `ctest` suites of `app/` and `sample/`. It installs
`pytest-asyncio` because `pyproject.toml` sets `asyncio_mode = "auto"`. The
GPU profile campaigns are not part of CI. A second job, `smoke`, is covered
under [End-to-end smoke test](#end-to-end-smoke-test-test-compose-profile).

Run all of them at once (`tests/integration` needs the smoke stack and
fails without it, so leave it out):

```bash
podman compose run --rm devtools pytest tests/ -v --ignore=tests/integration
```

Or just one component while you're actively working on it:

```bash
podman compose run --rm devtools pytest tests/tools -v
podman compose run --rm devtools pytest tests/agent -v
podman compose run --rm devtools pytest tests/indexer -v
podman compose run --rm devtools pytest tests/mcp -v
```

Without a container, run pytest from the repository root, after
`python scripts/host/install_deps.py` (it creates a virtualenv, `.venv` by
default; add `--trainer` for the trainer's own requirements):

```bash
python -m pytest -q --ignore=tests/integration
```

Each suite adds its own service directory to `sys.path` (in a
`conftest.py` for `agent`, `indexer`, `mcp` and `scripts`, in the test files
for most of the others), so no install step or `PYTHONPATH` is needed, but the
working directory must be the repo root.

A live checkout is a different check. `python scripts/deploy.py eval`
calls `POST /lookup` on a registered tree and reports whether the expected
file is in the final 5, only in the prefetch, or absent. It does not call
the chat model. See [Retrieval baseline](../notes/verification/retrieval-baseline.md).

The `tools` service's tests deserve special attention if you're touching
that code, `tests/tools/test_path_validation.py` is the most
security-critical suite in the repo (path traversal, symlink escapes,
Windows drive-anchor smuggling, null bytes). If you add a new path-taking
endpoint, add rejection tests for every category in that file before
anything else.

## C++ unit tests (ctest)

Tests are Catch2 v3 (`TEST_CASE` / `REQUIRE`). CMake's
`catch_discover_tests` registers each case as its own ctest entry, so
`ctest -R json_round_trip` (or `-R request_url`) runs a single case. The
framework comes from Ubuntu 24.04's `catch2` apt package, not FetchContent
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

## Trying the client by hand

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
`state/profile-campaign.json` (it backs `.env` up to
`state/profile-campaign.env.bak` and restores it). Questions and pass rules are in
[RTX 6000 scenarios](../notes/verification/rtx6000-scenarios.md). GitHub Actions does not run it.

## EVO-X5 host serve

`tests/scripts/test_evo_x5_serve.py` checks the `llama-server` command
for the GMKtec EVO-X5 Pro. It does not start the binary. Deploy steps
are in [Deploy on the EVO-X5](evo-x5.md). GitHub Actions does not boot
that machine.

`python scripts/host/profile_campaign.py --profiles evo-x5` plans that
host profile. `--run --workspace <registered checkout>` starts host
`llama-server`, recreates `agent`, stops the `llm` container if it runs, and
does not recreate it. Prompts are `scripts/host/evo_campaign_prompts.json` (edit
that file for another tree). Ids `S1` to `S5` keep the sample rubric. Any
other id stores the wall clock, llama-server `/metrics` deltas, and the
answer. The report is `state/profile-campaign.json`.

## Functional check suite (`python scripts/deploy.py check`)

Runs `scripts/host/functional_check.py` against the live stack: 25 steps
covering the host doors, authentication, checkout isolation, the model
catalog, `/lookup`, bounded reads and path-traversal rejection, `check`
(compile, build, test), the LSP `outline`/`calls`/`types` calls on `tools`,
the MCP `tools/list` + `tools/call` (`lookup`, `calls`, `types`), cited `/chat`, `/chat/stream` SSE, the
working-copy overlay, and `/metrics`. By default it targets `sample/`
(`--workspace` for another registered checkout). See
[Tests fonctionnels](../notes/verification/functional-checks.md).

## Tool-calling evaluation (`scripts/container/eval_tools.py`)

Scores whether the agent's **first** tool call matches a fixture
(`question`, `expected_tool`, `expected_args_subset`), no LLM-as-judge.
Requires a running stack, a reachable agent (`AGENT_BASE_URL`, default
`http://agent:8090`, an internal name) and `AGENT_BEARER_TOKEN`. The
`devtools` service has neither the token nor a network that reaches `agent`,
so run it from `smoke-test`, which has both:

```bash
podman compose --profile test run --rm smoke-test python scripts/container/eval_tools.py
```

Scenarios live in `scripts/container/eval_tools_scenarios.json` (the
expected tools are currently `build`, `compile_file`, and `test`). Add one
when you change a tool name, a default argument, or the system prompt's
"verify before asserting" wording. No CI job runs it.

## RAG retrieval evaluation (`scripts/container/eval_rag.py`)

A golden-question check that the *right chunks get retrieved*, not an
exact-match check on the LLM's free-text answer (which isn't byte-stable).
Requires a running stack whose index covers `app/` (set
`CODEBASE_PATH=./app` in `.env`; `sample/` would not match the fixture):

```bash
podman compose up -d qdrant llm embedder tools agent mcp
podman compose --profile index run --rm indexer
podman compose --profile test run --rm smoke-test python scripts/container/eval_rag.py
```

Like `eval_tools.py`, it reads `AGENT_BASE_URL` and `AGENT_BEARER_TOKEN`
from the environment, which `smoke-test` sets from `.env`. `agent` waits for
`serena` (`depends_on`), so the first `up` also pulls its image.

Add new golden questions to `scripts/container/eval_rag_questions.json` as the
codebase under `app/` grows, each entry's `expected_file_substring` is
matched against the `file_path` field of the agent's returned `sources`
(which, per `CONTRACTS.md`, is relative to `app/` with no `app/` prefix,
for example `src/main.cpp`, not `app/src/main.cpp`).

## End-to-end smoke test (`test` compose profile)

Or run `python scripts/deploy.py test` to do all of the below in one command
(including teardown, even on failure).

Brings up the full stack with a **tiny, CPU-only chat model** swapped in for
`llm` (the embedder is never swapped. See `CONTRACTS.md`) so this runs
quickly without a GPU, then runs `tests/integration/test_smoke.py` against
it from inside the `smoke-test` service (built from the same `devtools`
image, attached to every internal network so it can probe each service
directly on each service, including services other than `agent`).

```bash
# One-time: fetch the tiny CI model (and the real embedder model, which the
# test profile still needs - see CONTRACTS.md). `run` with an explicit
# command overrides the service's default one, so pass the --test flag this
# way rather than appending it.
podman compose --env-file .env.test --profile setup run --rm fetch-models \
  python scripts/container/fetch_models.py --test

# Build the CPU-only `llm` image and bring the stack up.
podman compose --env-file .env.test build llm embedder tools agent mcp
podman compose --env-file .env.test up -d qdrant llm embedder tools agent mcp ui-proxy

# Run the smoke test.
podman compose --env-file .env.test --profile test run --rm smoke-test

# Tear down.
podman compose --env-file .env.test down
```

What it checks (see the module docstring in `tests/integration/test_smoke.py`
for the full rationale):

- every service's health endpoint responds within a generous startup window
  (`qdrant`, `llm`, `embedder`, `tools`, `agent`, `mcp`, `ui-proxy`)
- `GET /models` and `GET /ui` (CSP) work; ui-proxy `/health` is reachable
  on the internal network
- `POST /lookup` returns 200 with a `hits` list on an empty collection
  (RAG wiring, no indexer in this profile)
- MCP Streamable HTTP `tools/list` includes `lookup`, `check`, `calls`,
  `types`, `ask`
- `agent`'s bearer-token auth actually rejects missing/wrong tokens
- a basic `/chat` round-trip works and degrades correctly with **no**
  indexed context (this stack's Qdrant collection starts empty)
- a tool-call round-trip completes without erroring (whether the tiny model
  *chooses* to call a tool is logged but not asserted, small models are
  known to be unreliable at structured tool-calling; that's precisely why
  `CONTRACTS.md` mandates a 7B+ model at Q5_K_M for production)

The stack also starts `serena` (pulled from `ghcr.io`, so the runner needs
internet access): `agent` has it in `depends_on`. The test does not probe it
directly, but a dead sidecar keeps `agent` from becoming healthy. `.env.test`
sets `CODEBASE_PATH=./app` and leaves `reranker` out.

This is the only test that actually exercises the real network segmentation,
healthcheck ordering, and inter-service wiring rather than a mocked version
of it. In CI it is the `smoke` job: it runs `python scripts/deploy.py test`,
then the RAG golden-question eval, on pushes to `main`/`master`, nightly, and
on manual dispatch, not on every pull request (the `unit` job covers those).
