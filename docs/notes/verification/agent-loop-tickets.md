# Tickets, agent loop

Design: [Agent loop](../../architecture/agent-loop.md).
There is no issue tracking on origin.cursor.com: these identifiers are the tickets.

**Status: AL-1 to AL-11 are done.** The design describes the shipped code,
with no known gaps. This page keeps the history of the breakdown (who touched
which files, in what order). The following tickets, on the Serena migration,
are under `.scratch/serena-sidecar/` (spec and issues 01 to 11).
Since that migration, "write" in the columns below means
a Serena write tool (`begin_plan`, `edit` and `apply` are no longer
names the model can call). The done criteria below are
the original ones; the current behavior is the one in the design.

Two groups could proceed at the same time. Within a group,
the tickets do not touch the same files. Group B waits for
group A only for AL-7, which wires in what A prepared.

## Group A, parallelizable

None of these tickets edits `tool_loop.py` or `main.py`.

| ID | Status | Files | Done when |
| --- | --- | --- | --- |
| AL-1 | done | `CONTRACTS.md`, `.env.example`, `.env.test`, `compose.yaml` | The three variables are in the contract, the example and the `agent` service. |
| AL-2 | done | `conversations.py`, `journal.py` | The next turn sees the trace. `old_string` is absent. The last verification failure survives 12 reads. |
| AL-3 | done | `token_budget.py` | An overlong `read_file` is shortened before the last `build_step`. |
| AL-4 | done | `edit_policy.py` | A failed `build_step` does not bring the phase back to `idle`. The refusal of `build` during `editing` is unchanged. |
| AL-5 | done | `llm_client.py` | `finish_reason` is readable. `name` is not sent to the model. |
| AL-6 | done | `services/tools`, `services/patch` | `POST /cancel` answers 204 and kills the process of the `X-Request-Id` along with those it launched. The `subprocess.run` seam of the tool tests must remain patchable. |
| AL-9 | done | `prompts.py` | The prompt says to follow `repair`, to call `build_step` after a write (`apply` originally, a Serena tool today), and that a call written in the text does not run. |
| AL-10 | done | `services/agent/ui/index.html` | The `nudge` event adds a line and clears the draft. |

AL-6 is in this group because it does not touch the loop. The
`subprocess.run` seam of the tool tests remains the patch point. It is
coded separately, not in AL-7.

## Group B, sequential

AL-7 is done in a single change. AL-8 after AL-7. AL-11 last,
when the code says the same thing as the design.

| ID | Status | Depends on | Files | Done when |
| --- | --- | --- | --- | --- |
| AL-7 | done | AL-2, AL-4, AL-5 | `tool_loop.py`, `tests/agent/test_tool_loop.py` | Open failure, replay, malformed, `no_tool`, disguised call, `length`, 4th batch. |
| AL-8 | done | AL-7, AL-2, AL-6 | `main.py`, `tests/agent/test_turns.py` | `remember` receives the trace. `nudges` is in the answer. A second `/chat` with the same id waits. Cancellation calls `POST /cancel`. |
| AL-11 | done | AL-7 | `docs/concepts/tool-calling.md`, `docs/concepts/en-clair.md` | Both pages describe the shipped turn exit, not the old one. |
