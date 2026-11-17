# Prompt, functional tests

Two levels: an automatic end-to-end check, then a manual campaign for an
MCP client. Both target `sample/` (the integer calculator, with
`known_bug:` cases that deliberately fail).

## Automatic check

Against the running stack:

```bash
python scripts/deploy.py check [--agent URL] [--workspace PATH] [--timeout SECONDS]
```

Prerequisites:

- the stack is running (`python scripts/deploy.py up`);
- `AGENT_BEARER_TOKEN` is in `.env` (otherwise the script exits with code `1`);
- `sample/` is registered: `python scripts/deploy.py workspace sample`.
  The script sends its path in `X-Workspace`; an unregistered path gives
  HTTP 400.

Defaults: `--agent http://127.0.0.1:8090`, `--workspace` = the repository's
`sample/`, `--timeout 120`. The script
`scripts/host/functional_check.py`, run directly, also accepts
`--mcp` (`http://127.0.0.1:8092`) and `--llm` (`http://127.0.0.1:8080`);
`deploy.py check` keeps their defaults.

Each step prints `[OK]` or `[FAIL]`, its name, and a detail. The last
line is `[check] N/M passed.`; the exit code is `0` if everything passes,
`1` otherwise. Step 23 modifies then restores
`sample/src/calculator.hpp`; it is skipped if that file does not exist
in the folder passed to `--workspace` (M is then 24).

The 25 checks:

1. **Host gates & `/ui` CSP**: `GET /health` on the agent, `mcp` and `llm`, and `GET /ui` as `text/html` with the `Content-Security-Policy` header.
2. **Bearer authentication**: `401` with no token and with a wrong token.
3. **Checkout isolation**: `400` on an unregistered `X-Workspace`.
4. **Model catalog**: `GET /models` returns one active model.
5. **`lookup`, declaration**: `Evaluate` first, at `src/calculator.hpp:23`, with no `.vscode` or `CMakePresets.json`.
6. **`lookup`, hybrid trace + reranker**: `include_candidates=True` returns `candidates`, `reranked` and `reranker_used`.
7. **`lookup`, missing symbol**: `Zorglub42` does not invent any symbol.
8. **Bounded read (`POST /read`)**: `start_line`..`end_line` window (18 to 23).
9. **Path traversal**: `../../etc/passwd` is rejected (400, 404 or 422).
10. **`check`, `compile`**: `src/calculator.cpp`, exit code `0`.
11. **`check`, `build`**: `release` preset, exit code `0`.
12. **`check`, `test`**: `filter=known_bug` fails and the log contains `known_bug`.
13. **`POST /lsp/outline`**: `src/calculator.hpp` contains `CalcError class 18` and `Evaluate function 23`. Agent route, no longer an MCP tool.
14. **`POST /lsp/calls`**: `src/calculator.cpp:118:5`, one caller in `tests/calculator_test.cpp` (see the note below).
15. **`POST /lsp/types`**: `src/calculator.hpp:18:7`, `runtime_error` in the output.
16. **MCP Streamable HTTP (`:8092/mcp`)**: `tools/list` contains `lookup`, `check`, `calls`, `types`, `ask`, then a `tools/call` on each.
17. **`/chat`, direct route**: `2+2` with no index (`used_context` false).
18. **`/chat`, code route with citations**: the declaration of `Evaluate` cites `src/calculator.hpp`.
19. **`/chat`, multi-turn memory**: a `conversation_id` returns the code word given in the previous turn.
20. **`/chat`, bounded judgment**: question about `ParseExpression` with `enable_tools=False`; expected `tool_calls == []` and an answer that cites `calculator.cpp` (see the note below).
21. **`/chat`, tool-call loop**: the model calls `list_dir` (Serena) and the answer contains `calculator.cpp`.
22. **`/chat/stream` + `/chat/cancel`**: events `meta`, `route`, `context`, `token`, `done`; a `cancel` on an inactive conversation returns `stopped: false`.
23. **`[working copy]` overlay**: a line added to `calculator.hpp` without a commit comes back with `source=working_copy`, then the file is restored.
24. **Concurrent lookups**: two `POST /lookup` in parallel, both with hits.
25. **`GET /metrics`**: Prometheus, through the same gate as `/ui`.

Two known discrepancies in the script, to fix in `scripts/host/functional_check.py`:

- Step 14 targets line 118. That is the definition of `Evaluate` at the last
  commit of `sample/`; with the uncommitted change (`%` operator),
  it is on line 122 and line 118 is the end of the `Parser` class.
- Step 20 sends `enable_tools` and `max_tokens`, but the fields of
  `POST /chat` are `tools` and `max_reply_tokens`
  ([Agent API](../contracts/agent-api.md)). Unknown keys are
  ignored: tools stay on, and the step only verifies "without
  tools" if the model does not call any.

---

## Manual campaign from an MCP client

To test by hand from an MCP client (VS Code, Continue), copy the
block below. One step per message. Copy the tool's text verbatim.
Do not invent a result.

---

You are testing this repository's local tool, not the calculator. The repository under test is `sample/` (integer calculator). The stack must already be responding. If a service is unreachable, stop and say so.

## Before any question

1. Check that these URLs respond on the host machine:, `http://127.0.0.1:8090/ui`, `http://127.0.0.1:8092/mcp`, `http://127.0.0.1:8080/v1/models`
2. The MCP checkout is `sample` (`X-Workspace` header or `python scripts/deploy.py workspace` pointed at `sample/`).
3. The ticked tools are `lookup`, `check`, `calls`, `types`, `ask`. If only `ask` is there, stop: the test measures nothing.

## Pass rule

At each step, call **one** tool only, with the given arguments, and copy its output as is. A sentence that narrates, an invented symbol, or going through `ask` when the step names another tool: the step **fails**. Stop the campaign at the first failure of this kind. The following steps would tell you nothing.

`ask` is used only in the step that names it. It does not search, does not compile, does not list symbols.

## Steps

**1. lookup, declaration.** Tool `lookup`. Question verbatim: `où est déclarée Evaluate ?` (where is `Evaluate` declared?)

Expected: `src/calculator.hpp` and the line of `int Evaluate(std::string_view expression);` (line 23). The `int Evaluate` body in `src/calculator.cpp` is not the declaration. No `.vscode` file or `CMakePresets.json` at the top if the question does not name them.

**2. lookup, absent.** Tool `lookup`. Question verbatim: `où est déclaré Zorglub42 ?` (where is `Zorglub42` declared?)

Expected: no symbol, no invented file. A clear absence.

**3. check, oracle.** Tool `check`, action `test`, filter `known_bug`.

Expected: a test log (ctest), not a summary, starting with `test exit <code>`. The `known_bug:` cases fail on the current code. If the output is a written list of bugs with no `test exit` and no `TEST_CASE` name, it fails.

**4. calls.** Tool `calls`, path `src/calculator.cpp`, line = the line of `int Evaluate(std::string_view expression) {` (`rg -n "int Evaluate" sample/src/calculator.cpp`: 118 at the last commit, 122 with the uncommitted `%` change), character `5`, direction `both`.

Expected: lines `name path:line` (callers or callees, including one in `tests/calculator_test.cpp`), or an explicit absence. Not a description of the function. If the output says `compile_commands.json` is missing or the CMake configure failed, note **clangd unavailable**, mark step 5 not run and continue with step 6.

**5. types.** Tool `types`, path `src/calculator.hpp`, line `18`, character `7`, direction `both`.

Expected: `CalcError` linked to `std::runtime_error`, with a path and a line. Not a sentence about inheritance without a position.

**6. ask, bounded judgment.** Tool `ask` only. Question verbatim: `Le code de sample montre-t-il que 2+3*4 vaut 14 ? Cite path:ligne. Si le code ne le montre pas, dis-le.` (Does the sample code show that 2+3*4 equals 14? Cite path:line. If the code does not show it, say so.)

Expected: at most a short answer, `path:line` citations that exist in the repository, or the sentence saying the code does not show it. No tool call in the text, no `lsp_*` or `compile_file` JSON, no invented diff, no `Sources:` citing a missing file. The current body computes `2+3*4` as 20 (that is the `known_bug` case); an `ask` that claims 14 without a repository line proving it fails.

**7. /ui page.** Open `http://127.0.0.1:8090/ui`, paste the token and send: `Que vaut 2+2 ?` (What is 2+2?)

Expected: the trace shows `Question générale … réponse directe` (general question … direct answer), then tokens as they stream, then an answer. A block that only appears at the end fails. A code question (`Où est déclarée Evaluate ?`, "Where is Evaluate declared?") must show `Question sur le dépôt` (question about the repository) and the retrieved context before the tokens; if the model calls a tool, the trace goes through `Outil en cours` (tool running) then `Outil réussi` (tool succeeded) or `Outil en échec` (tool failed). The page answers on the default folder (`CODEBASE_PATH`, `./sample` by default), not on the MCP checkout.

**8. Dirty file.** In `sample/src/calculator.hpp`, add a comment line at the top, without a commit. Rerun step 1.

Expected: the `calculator.hpp` chunk is marked `[working copy]` and contains that comment. The shared index is not enough if it shows the old text.

Put the file back as it was (`git checkout` or equivalent) at the end of this step. Careful: `sample/` may contain uncommitted changes that are not yours (`git diff -- sample`); remove only the line you added.

## Report

Table, one row per step:

| Step | Status | Evidence |
|---|---|---|
| 1 … 8 | ok, failed, or not run | One quoted line of the tool output, or the exact error |

Allowed statuses: `ok`, `failed`, `not run`. No other column. No fix proposed in this report.
