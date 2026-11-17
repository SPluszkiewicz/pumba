# Adding a new tool

The `tools` service (`services/tools/`) deliberately has **no generic
shell/exec passthrough**, every endpoint is a fixed, parameterized route
with its own validation. Adding a new one means adding a new curated
endpoint, not exposing a new degree of freedom to the model. Read
[Security](../architecture/security.md) before starting: an LLM decides
when to call this service, so treat every input as hostile.

This walks through the four things a new **HTTP named tool** needs (one
that runs in `tools`, like `build_step` or `git_log`), using `lint` as the
running example. Reads, searches, symbol navigation, and edits are Serena's
job: see [Adding a Serena named tool](#adding-a-serena-named-tool) at the
end, and [Tool calling](../concepts/tool-calling.md) for the 16 + 23 split.

## 1. Write the handler

Add `services/tools/handlers/<your_tool>.py`. Every existing handler
follows the same shape, a single `run_<your_tool>(...)` function that:

1. Validates its own inputs and raises a `ValueError` subclass **before**
   touching a subprocess, for anything that isn't already covered by
   `path_safety` (e.g. `lint.LintValidationError` for an empty `paths` list).
2. Routes every caller-supplied path through
   `services.tools.handlers.path_safety.validate_path(raw_path, project_root)`, never touch `Path` objects built from raw strings any other way. If
   your tool doesn't take a path at all, say so explicitly in a comment; don't
   silently skip this step.
3. Builds an **argv list**, never a shell string, `["clang-tidy", path]`,
   not `f"clang-tidy {path}"`.
4. Calls `subprocess.run(argv, cwd=..., capture_output=True, text=True,
   timeout=..., shell=False, check=False)`, `shell=False` is not optional.
5. Returns a plain `dict` with the four base fields
   (`ok`/`stdout`/`stderr`/`exit_code`) plus whatever endpoint-specific
   fields make sense (see `docs/notes/contracts/tools-api.md` for the existing
   ones), and handles `subprocess.TimeoutExpired` explicitly rather than
   letting it propagate as an unhandled exception.

`services/tools/handlers/lint.py` is the shortest complete example that
validates paths. `services/tools/handlers/build.py` is the example that
takes no caller-supplied path (the preset is an allow-listed name, and the
tree is the active checkout).

If your tool needs a fresh scratch directory per request (like `/build` and
`/test` do), reuse `build.run_cmake_pipeline`'s pattern: `tempfile.mkdtemp()`
in a `try`, `shutil.rmtree(..., ignore_errors=True)` in the matching
`finally`, never skip the cleanup, and never reuse a scratch dir across
requests.

## 2. Wire it into `main.py`

In `services/tools/main.py`:

1. Add a pydantic argument model in **`services/common/tool_models.py`**
   (subclass `ToolArgs`, give every field a `description` written for the
   LLM and `ge`/`le` bounds where relevant), plus a `ToolSpec` entry in
   `TOOL_SPECS` (function name, HTTP method, path, description, model).
   That one entry is what both the `tools` route *and* the LLM-facing
   schema are built from. There is nothing to duplicate in the agent.
   Leave the `ToolSpec` out if the route is for the agent's own use (as
   `/git/status`, `/git/diff-names`, and `/read-file` are): without a spec
   the model cannot name it.
2. Add the route, calling straight into your handler function, routes
   should be thin, all the logic lives in the handler. Declare it with a
   plain `def`, **not** `async def`: the handler calls the blocking
   `subprocess.run`, and FastAPI only moves plain `def` routes to its thread
   pool. An `async def` route would freeze the whole service (and its
   `/health`) for the length of the command.
   `tests/tools/test_routes_non_blocking.py` fails if you forget. Use
   `active_root()`, not `PROJECT_ROOT`, so a registered checkout selected
   by `X-Project-Root` is honoured:

   ```python
   @app.post("/your-endpoint")
   def your_endpoint(request: YourArgs) -> dict:
       return your_tool.run_your_tool(request.field, active_root(), YOUR_TIMEOUT_SECONDS)

   # GET endpoints take the same model as a query-string model:
   @app.get("/your-endpoint")
   def your_endpoint(args: Annotated[YourArgs, Query()]) -> dict: ...
   ```

3. If your handler raises a new exception type that needs a specific HTTP
   status code other than the generic `400` from the catch-all
   `ValueError`/`PathSafetyError` handlers already registered), add a new
   `@app.exception_handler(...)` for it, following the pattern used for
   `read_file.ReadFileNotFoundError` (`404`) and
   `read_file.FileTooLargeError` (`413`).

## 3. Write tests

Add `tests/tools/test_<your_tool>_handler.py`. At minimum, cover:

- **Input validation rejects bad input before any subprocess runs**, patch
  `services.tools.handlers.<your_tool>.subprocess.run` with
  `unittest.mock.patch` and assert `mock_run.assert_not_called()` inside the
  `pytest.raises(...)` block.
- **Argv is a list, `shell=False`**, assert on `mock_run.call_args.args[0]`
  (a list) and `mock_run.call_args.kwargs["shell"] is False`. See
  `tests/tools/test_argv_construction.py` for the cross-handler version of
  this, and add your new handler to it.
- **A malicious-looking input passes through inertly**, e.g. a pattern or
  path segment containing `; rm -rf /` or `$(...)` should show up as
  exactly one literal argv element, never interpreted (there's no shell to
  interpret it, but the test should prove that structurally).
- **Timeout handling**, mock `subprocess.run` to raise
  `subprocess.TimeoutExpired`, and assert the response comes back with
  `ok: false` and the conventional timeout exit code
  (`services.tools.handlers.build.TIMEOUT_EXIT_CODE`, currently `124`).
- If your tool touches paths, you don't need to re-test `path_safety.py`
  itself (that's `tests/tools/test_path_validation.py`'s job), just assert
  your handler actually calls `validate_path` (e.g. a `../` traversal
  attempt in one of your fields raises before any subprocess runs).

Run the suite from the repo root. `pyproject.toml` sets
`asyncio_mode = auto` and the `tests/<service>/conftest.py` files put the
service directory on `sys.path`; the `tests/tools` files also insert the
repo root themselves (`from services.tools.handlers import ...`), so copy
that boilerplate from an existing test file:

```bash
python -m pytest tests/tools/ -v
```

## 4. Register it with the agent

Nothing to do by hand: `agent`'s `tools_schema.py` derives the OpenAI-style
function-calling schema and the name -> HTTP mapping (`TOOL_ENDPOINT_MAP`)
from `TOOL_SPECS` (step 1 above), merges them with the Serena names into the
39-name schema, and `tool_loop.py` validates the LLM's arguments with your
model before calling the endpoint. Things to check:

1. The function `name` you picked is a valid identifier (underscores, no
   slashes), `lint_clang_tidy`, not `lint/clang-tidy`, and does not
   collide with a Serena name.
2. `tests/common/test_tool_models.py` passes: it compares `TOOL_SPECS` with
   the OpenAPI document of the `tools` app (route exists, same method, every
   field accepted), and `tests/agent/test_prompts.py`
   requires the system prompt in `services/agent/prompts.py` to mention
   every name in `TOOL_ENDPOINT_MAP`, the HTTP names only.
3. If the new tool changes files or must run only after a build, decide
   whether it belongs in the verify set (`VERIFY_TOOLS` in
   `services/agent/journal.py`), which controls how a failure of it is
   closed (only by a success of the same name).

## Adding a Serena named tool

A Serena name is a different path: the call goes to the sidecar, not to a
`tools` route, so there is no handler, no route, and no `ToolSpec`. Instead:

1. Add a `ToolArgs` model and a `SerenaNamedTool(name, description, model)`
   entry in `services/agent/serena_named_tools.py`. The `name` must be a
   tool the sidecar really exposes (and not one that
   `config/serena/serena_config.yml` excludes). Set `writes=True` for
   anything that changes files: that adds the name to `SERENA_WRITE_NAMES`,
   which is what opens the *editing* phase (`edit_policy.py`) and makes
   `build`/`test`/`compile_file` wait for a `build_step`.
2. Name every path argument `relative_path`, `reference_file`,
   `paths_include_glob`, or `paths_exclude_glob`, because
   `services/agent/serena_policy.py` only checks those. Add a new path-like
   argument name to the policy in the same change, with a test.
3. Cover it in the agent tests (`tests/agent/test_serena_transport.py`,
   `tests/agent/test_edit_policy.py`, and the policy tests), run
   `python -m pytest tests/agent tests/common -q`, and update the lists in
   [Tool calling](../concepts/tool-calling.md) and `CONTRACTS.md` ("Named
   tools"). `tests/agent/test_prompts.py` does not check Serena names, so
   mention the tool in `services/agent/prompts.py` yourself if the model
   needs guidance for it.

## Checklist

- [ ] Handler validates every input and never builds a shell string
- [ ] Every caller-supplied path goes through `path_safety.validate_path`
      (or there's a comment explaining why not, like `search.py`'s)
- [ ] `subprocess.run(..., shell=False)` with a real timeout
- [ ] Scratch dirs (if any) are cleaned up in a `finally`
- [ ] Tests: rejection-before-subprocess, argv-is-a-list, inert malicious
      input, timeout handling
- [ ] Route added to `main.py` as a plain `def` (not `async def`), plus an exception handler if you introduced
      a new exception type needing a non-`400` status
- [ ] `docs/notes/contracts/tools-api.md` updated with the new endpoint
- [ ] `ToolSpec` + argument model added in `services/common/tool_models.py`,
      tool mentioned in `services/agent/prompts.py`,
      `tests/common/test_tool_models.py` and `tests/agent/test_prompts.py` green
- [ ] `CONTRACTS.md` ("Named tools", "`tools` endpoints") and
      `docs/concepts/tool-calling.md` list the new name and route
