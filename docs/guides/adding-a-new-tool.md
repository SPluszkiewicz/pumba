# Adding a new tool

The `tools` service (`services/tools/`) deliberately has **no generic
shell/exec passthrough** - every endpoint is a fixed, parameterized route
with its own validation. Adding a new one means adding a new curated
endpoint, not exposing a new degree of freedom to the model. Read
[Security](../architecture/security.md) before starting: an LLM decides
when to call this service, so treat every input as hostile.

This walks through the four things a new tool needs, using the existing
`search` endpoint as the running example.

## 1. Write the handler

Add `services/tools/handlers/<your_tool>.py`. Every existing handler
follows the same shape - a single `run_<your_tool>(...)` function that:

1. Validates its own inputs and raises a `ValueError` subclass **before**
   touching a subprocess, for anything that isn't already covered by
   `path_safety` (e.g. `search.SearchValidationError` for an empty
   `pattern`, `lint.LintValidationError` for an empty `paths` list).
2. Routes every caller-supplied path through
   `services.tools.handlers.path_safety.validate_path(raw_path, project_root)`
   - never touch `Path` objects built from raw strings any other way. If
   your tool doesn't take a path at all (like `search`, which always
   searches the fixed project root), say so explicitly in a comment; don't
   silently skip this step.
3. Builds an **argv list**, never a shell string - `["rg", "--fixed-strings",
   pattern, str(project_root)]`, not `f"rg --fixed-strings {pattern} ..."`.
4. Calls `subprocess.run(argv, cwd=..., capture_output=True, text=True,
   timeout=..., shell=False, check=False)` - `shell=False` is not optional.
5. Returns a plain `dict` with the four base fields
   (`ok`/`stdout`/`stderr`/`exit_code`) plus whatever endpoint-specific
   fields make sense (see `docs/reference/tools-api.md` for the existing
   ones), and handles `subprocess.TimeoutExpired` explicitly rather than
   letting it propagate as an unhandled exception.

`services/tools/handlers/search.py` is the shortest complete example if
your new tool also doesn't take a caller-supplied path (e.g. it operates
only on the fixed project root, like a "list files" tool might).
`services/tools/handlers/lint.py` is the shortest example that *does*
validate paths.

If your tool needs a fresh scratch directory per request (like `/build` and
`/test` do), reuse `build.run_cmake_pipeline`'s pattern: `tempfile.mkdtemp()`
in a `try`, `shutil.rmtree(..., ignore_errors=True)` in the matching
`finally` - never skip the cleanup, and never reuse a scratch dir across
requests.

## 2. Wire it into `main.py`

In `services/tools/main.py`:

1. Add a pydantic argument model in **`services/common/tool_models.py`**
   (subclass `ToolArgs`, give every field a `description` written for the
   LLM and `ge`/`le` bounds where relevant), plus a `ToolSpec` entry in
   `TOOL_SPECS` (function name, HTTP method, path, description, model).
   That one entry is what both the `tools` route *and* the LLM-facing
   schema are built from - there is nothing to duplicate in the agent.
2. Add the route, calling straight into your handler function - routes
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
   status code (not just a generic `400` via the catch-all
   `ValueError`/`PathSafetyError` handlers already registered), add a new
   `@app.exception_handler(...)` for it, following the pattern used for
   `read_file.ReadFileNotFoundError` (`404`) and
   `read_file.FileTooLargeError` (`413`).

## 3. Write tests

Add `tests/tools/test_<your_tool>_handler.py`. At minimum, cover:

- **Input validation rejects bad input before any subprocess runs** - patch
  `services.tools.handlers.<your_tool>.subprocess.run` with
  `unittest.mock.patch` and assert `mock_run.assert_not_called()` inside the
  `pytest.raises(...)` block.
- **Argv is a list, `shell=False`** - assert on `mock_run.call_args.args[0]`
  (a list) and `mock_run.call_args.kwargs["shell"] is False`. See
  `tests/tools/test_argv_construction.py` for the cross-handler version of
  this, and add your new handler to it.
- **A malicious-looking input passes through inertly** - e.g. a pattern or
  path segment containing `; rm -rf /` or `$(...)` should show up as
  exactly one literal argv element, never interpreted (there's no shell to
  interpret it, but the test should prove that structurally, not just
  assert "nothing bad happened").
- **Timeout handling** - mock `subprocess.run` to raise
  `subprocess.TimeoutExpired`, and assert the response comes back with
  `ok: false` and the conventional timeout exit code
  (`services.tools.handlers.build.TIMEOUT_EXIT_CODE`, currently `124`).
- If your tool touches paths, you don't need to re-test `path_safety.py`
  itself (that's `tests/tools/test_path_validation.py`'s job) - just assert
  your handler actually calls `validate_path` (e.g. a `../` traversal
  attempt in one of your fields raises before any subprocess runs).

Run the suite from the repo root. The root `pytest.ini` sets
`asyncio_mode = auto` and the `tests/<service>/conftest.py` files put the
service directory on `sys.path`; the `tests/tools` files also insert the
repo root themselves (`from services.tools.handlers import ...`), so copy
that boilerplate from an existing test file:

```bash
python -m pytest tests/tools/ -v
```

## 4. Register it with the agent

Nothing to do by hand: `agent`'s `tools_schema.py` derives the OpenAI-style
function-calling schema and the name -> HTTP mapping from `TOOL_SPECS`
(step 1 above), and `tool_loop.py` validates the LLM's arguments with your
model before calling the endpoint. Two things to check:

1. The function `name` you picked is a valid identifier (underscores, no
   slashes) - `lint_clang_tidy`, not `lint/clang-tidy`.
2. `tests/common/test_tool_models.py` passes: it compares `TOOL_SPECS` with
   the `tools` app's OpenAPI document (route exists, same method, every
   field accepted), and `tests/agent/test_prompts.py` requires the system
   prompt in `services/agent/prompts.py` to mention the new tool.

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
- [ ] `docs/reference/tools-api.md` updated with the new endpoint
- [ ] `ToolSpec` + argument model added in `services/common/tool_models.py`,
      tool mentioned in `services/agent/prompts.py`,
      `tests/common/test_tool_models.py` green
