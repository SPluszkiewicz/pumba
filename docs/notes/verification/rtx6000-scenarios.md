# RTX 6000 scenarios

Run these on the machine that has the RTX 6000, not on the 16 GB 4080.
The goal is to compare profiles, not to re-verify the MCP wiring.

The script `scripts/host/profile_campaign.py` follows these scenarios. Without
`--run`, it only prints the profiles that match `nvidia-smi`.
With `--run`, it applies each profile, recreates `llm`, sends S0 to S5 to
the agent and writes `state/profile-campaign.json`. Before the first profile,
`--run` copies `.env` to `state/profile-campaign.env.bak` and restores it at
the end, but the `llm` container stays on the last profile run: to
keep it, run `gpu-profile <name>` again, then recreate `llm`. This is not a
GitHub Actions job. The `evo-x5` profile is the other path, described in
[Deploy on the EVO-X5](../../guides/evo-x5.md): `llama-server` on the host, not this
container.

```text
python scripts/host/profile_campaign.py
python scripts/host/profile_campaign.py --run --workspace D:/path/to/sample
```

The `lookup`, `check`, `calls` and `types` steps of
[Functional tests](functional-checks.md) do not go through the
chat model. Run them **once**, before the campaign, to confirm
that the stack responds. After that, each profile goes through the page
`http://127.0.0.1:8090/ui`, with a fresh conversation per scenario.

## Which profile, according to `nvidia-smi`

Read `memory.total`, not the task manager.

| `memory.total` | Profiles to compare | The others |
| --- | --- | --- |
| ~24576 MiB | `24g`, then `24g-30b` | `48g*` does not fit |
| ~46068 MiB (ECC) or 49152 | `48g`, `48g-30b`, `48g-32b` | `24g-30b` is pointless here: Q8 is the strong case |
| ~16376 MiB | none from this page | stay on `16g` |

`24g` and `48g` are the same 14B. Comparing them with each other measures the KV cache
(`q8_0` versus `f16`) and the headroom, not a different model. The expected gain
is `24g` versus `24g-30b`, or `48g` versus `48g-30b` and `48g-32b`.

## Before each profile

```text
python scripts/deploy.py fetch-extra 24g-30b
python scripts/deploy.py gpu-profile 24g-30b
```

`fetch-extra` only for `24g-30b`, `48g-30b`, `48g-32b`.
`16g`, `24g` and `48g` use the 14B already downloaded by `up`.

If `EMBEDDER_GPU_ARG` changes, rebuild the embedder, then recreate
`llm`, `embedder` and `reranker`. Otherwise recreating `llm` is enough.
Wait until `http://127.0.0.1:8080/v1/models` shows the profile's
identifier (file name without `.gguf`). A 0.5B or a 14B still
loaded invalidates the scenario.

Checkout: `sample`, already indexed. Put `sample/` back to clean between
two scenarios that touch a diff (`git checkout -- sample` or
equivalent). Do not commit.

## S0. It fits

Do this for each profile, before S1. A failure stops that profile.

1. `http://127.0.0.1:8080/health` responds.
2. `/v1/models` shows the right identifier.
3. On `/ui`, send `Réponds exactement : pong` (Answer exactly: pong).
4. During the answer, `nvidia-smi`: memory used, and whether the
   process spills into system memory.

| Result | Next |
| --- | --- |
| healthy, right model, answer `pong`, no spill | S1 to S5 |
| `llm` does not become healthy, or the process spills | profile eliminated. On 24 GB, go back to `24g` |

Note the MiB used. It is the only figure that separates `24g` and
`48g` for the same 14B.

## S1, a tool, not an opinion

Fresh conversation. Paste:

```text
Lance uniquement le cas ctest known_bug. Utilise les outils. Ne devine pas le résultat.
```

(Run only the ctest case known_bug. Use the tools. Do not guess the result.)

Passed: a `test` tool call with the `known_bug` filter, then an
answer that says those cases fail and that cites a `TEST_CASE` name from the
log. Failed: a list of bugs with no tool call, or tool
JSON written in the text.

## S2, citation

Fresh conversation. Paste:

```text
Le code de sample montre-t-il que 2+3*4 vaut 14 ? Cite path:ligne. Si le code ne le montre pas, dis-le.
```

(Does the sample code show that 2+3*4 equals 14? Cite path:line. If the code does not show it, say so.)

Passed: cites `tests/calculator_test.cpp` (the `REQUIRE` that expects 14)
and says that `src/calculator.cpp` does not compute this precedence. The current
`ParseExpression` handles `+` and `*` at the same level, so
`2+3*4` gives 20 there. Failed: claiming the program gives 14, citing a
missing file, or writing a tool call in the text.

## S3, absence

Fresh conversation. Paste:

```text
Où est déclaré Zorglub42 ? Si le dépôt ne le contient pas, dis-le.
```

(Where is Zorglub42 declared? If the repository does not contain it, say so.)

Passed: the repository does not contain it. Failed: an invented path or
symbol.

## S4, a diff that applies

Fresh conversation. Paste:

```text
Propose un seul diff unifié pour que le test « known_bug: multiplication binds tighter than addition » passe. Ne modifie pas le test. N'applique pas le diff.
```

(Propose a single unified diff so that the test "known_bug: multiplication binds tighter than addition" passes. Do not modify the test. Do not apply the diff.)

Save the `diff` block to a file. In `sample/`:

```text
git apply --check fix.diff
```

Passed: `--check` passes, and the hunk touches `src/calculator.cpp`, not
`tests/calculator_test.cpp`. Failed: the diff does not apply, the test
is modified to expect 20, or there is no `diff` block.

Do not apply the diff for the rest. Put the tree back to clean if a
trial modified it.

## S5, two files, one diff

Fresh conversation. Paste:

```text
Ajoute int Version() qui retourne 1 : déclaration dans src/calculator.hpp, définition dans src/calculator.cpp. Un seul diff unifié. N'applique pas le diff.
```

(Add int Version() that returns 1: declaration in src/calculator.hpp, definition in src/calculator.cpp. A single unified diff. Do not apply the diff.)

Same verification: `git apply --check`. Passed if both files
are in the diff, the declaration and the definition match, and
`--check` passes. Failed otherwise.

## Report

One row per profile that passed S0. The others: one `eliminated` row
and the reason (healthy, spill, wrong model loaded).

| Profile | MiB | S1 | S2 | S3 | S4 | S5 |
| --- | --- | --- | --- | --- | --- | --- |
| | | ok or failed | | | | |

The retained profile is the one with the most `ok` on S1 to S5 without a
spill. On a tie, keep the fastest to answer and the one with the fewest
MiB. `48g` does not beat `24g` if the five scenarios are
identical: it is the same 14B.
