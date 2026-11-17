# Review fixture

`app/` is the `llm-client`: the program that calls the agent. It is not the
project under review.

`sample/` is a small integer calculator. The header in
`sample/src/calculator.hpp` states the language. The implementation in
`sample/src/calculator.cpp` does not match that contract. Nothing in
`sample/` names the mistakes, so a review that only sees the mounted tree
cannot read this page.

The stack mounts `CODEBASE_PATH` (default `./sample`) at `/workspace/app`:
read-only for `agent` and `tools`, read-write for `serena`. A
question that asks for a fix gets a fenced `diff` block when it also says not
to apply it (the [RTX 6000 scenarios](../notes/verification/rtx6000-scenarios.md) do); otherwise the
agent may change `sample/` itself through Serena's write tools, which opens
**editing** until a `build_step` succeeds, and never commits. Either way,
run the oracle below again on the host. Optional notes for that tree live in
`sample/.llm/rules/*.md` (the indexer skips `.llm`). A field `instructions`
on `POST /chat` adds a note for one question.

## Known mistakes

| Expression | Current result | Contract |
|---|---|---|
| `2+3*4` | `20` | `14` (`*`, `/`, and `%` bind tighter than `+` and `-`) |
| `8-3-2` | `7` | `3` (`-` is left-associative; the code recurses on the right) |
| `2^3^2` | `64` | `512` (`^` is right-associative) |
| `5^0` | `0` | `1` |
| `1+-2`, `2*-3` | error "expected number" | `-1`, `-6` (unary minus is only parsed on the first atom) |
| `1/0` | `0` | `CalcError` |
| `2^(-1)` | `0` | `CalcError` |

## Oracle

Cases whose name starts with `known_bug` encode the contract. They fail on
the current code. `ctest --preset release` excludes them, so the image
build and CI stay green. Run the oracle without that preset filter:

```bash
cd sample
cmake --preset release
cmake --build --preset release
ctest --test-dir build/release -R known_bug --output-on-failure
```

After the implementation matches the header, that command passes. The
preset command keeps passing too.

## What `/review` can see

`POST /review` comments on a `git diff` between two refs. `sample/` has no
`.git` of its own, and the tools mount is only `CODEBASE_PATH`. Git history
is available when that path is a work tree (your checkout), not when it is
this fixture alone. Asking in `/ui` still reads and indexes `sample/`
directly.
