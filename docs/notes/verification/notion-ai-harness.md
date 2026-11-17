# The notion of "AI harness"

> **Status**: informative, no decision. Meaning of the word "harness" according to external pages read on 2026-11-10 (several "consulted without date"); nothing here describes the state of the repository, except the final pointer to the note on the harness's role. · **Last verified**: 2026-11-12

**Verdict:** the word covers two objects, held by different texts. Since 2021, at EleutherAI, a *harness* is the library that runs benchmarks on a model and computes its scores. From late 2025, Anthropic, OpenAI, and LangChain call *harness* the software around the model that holds the tool loop; each page sets its boundary, and these boundaries remain those of the page that writes them.

Research note of 2026-11-10.

## Question

What does "AI harness" mean (also "agent harness", "harness" around LLMs), who holds each meaning, and how do these meanings relate to an agent, an evaluation runner, a scaffold, and a tool loop? The reader of this repository already calls the local agent service a "harness". This note traces the external notion, starting from the pages that carry it.

## Short answer

| Meaning | Text that holds it | Date on the page | Named object | Relation |
| --- | --- | --- | --- | --- |
| Evaluation harness | EleutherAI, *Language Model Evaluation Harness* | Blog of 2021-10-11; the GitHub repository shows a creation on 2020-08-28; the v2 paper says the library dates from 2021 | Library that runs evaluation tasks on a model and aggregates metrics | Benchmark runner. A task: data, prompts, request to the model, post-processing, and score. |
| Patch evaluation harness | Princeton NLP repository `SWE-bench` | Changelog `[2.0.0]`, 2024-06-27 | Runner that applies a prediction and runs the tests in Docker | Same family: it grades a result. The paper arXiv:2310.06770 calls this setup an *evaluation framework*. |
| Agent harness | Anthropic, *Demystifying evals for AI agents* | 2026-01-09 | System that makes a model act: inputs, tool calls, results | The same page glosses it "or scaffold", and separates it from the *evaluation harness*. Evaluating "an agent" means evaluating the harness and the model together. |
| Agentic harness | Claude Code documentation, glossary | The page consulted does not date the entry | Tools, context management, execution environment | Provides file access, shell, permissions, memory loading, and the loop that chains the actions. Claude is the model inside it. |
| Harness (loop) | Anthropic, *Scaling Managed Agents* | 2026-04-08 | The loop that calls Claude and routes its tool calls | Separate, on this page, from the session and the sandbox. |
| Codex harness | OpenAI, *Unrolling the Codex agent loop* then *Unlocking the Codex harness* | 2026-01-23 and 2026-02-04 | The agent loop and execution logic under the Codex surfaces | The loop stops when the model stops emitting tool calls and produces a message. |
| Harness = contract around the model | OpenAI Cookbook, *Agent improvement loop* | The page consulted does not give a date | Instructions, tools, routing, output requirements, validation checks | The notebook sets this contract as the agent's current harness. |
| Harness = everything around the loop | LangChain documentation, *Agents* page | The page consulted does not give a date | Prompt, tools, middleware | Formula of the page: Agent = Model + Harness. |
| Harness = provided framework | LangChain documentation, *Runtimes, frameworks, and harnesses* | The page consulted does not give a date | Tools, prompts, and sub-agents already assembled (Deep Agents) | A different object from the previous row: LangChain is classed there as a framework, Deep Agents as a harness. |
| Cursor SDK harness | Cursor documentation, *Agent SDK* and *Agent config* | The pages consulted do not give a date | The loop and built-in tools that run Bugbot, Security Review, Approval Bot, and Rollouts | The caller writes instructions and tools. Cursor runs the loop that calls them. |
| Caller's eval harness | Cursor documentation, *Run Cursor in your evals* | The page consulted does not give a date | The caller's program that passes a task, gets back a transcript and a state, then grades | The product's agent loop runs inside this runner. |

## Evaluation harness: EleutherAI

The oldest meaning that the primary pages date is EleutherAI's. The repository page [EleutherAI/lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness/) shows `Created: 2020-08-28`. The current README titles the project *Language Model Evaluation Harness* and describes it this way:

> This project provides a unified framework to test generative language models on a large number of different evaluation tasks.

The EleutherAI post [*Multiple Choice Normalization in LM Evaluation*](https://blog.eleuther.ai/multiple-choice-normalization/) (Leo Gao, October 11, 2021) already uses the name, for the way the library scores a multiple-choice quiz: "This approach is used by the Language Model Evaluation Harness in all multiple choice tasks and presented as `acc`."

The authors' paper, revision read at [arXiv:2405.14782v2](https://arxiv.org/html/2405.14782v2), *Lessons from the Trenches on Reproducible Evaluation of Language Models*, fixes the origin and the role:

> lm-eval was originally built in 2021 and has been in continuous use at EleutherAI and elsewhere since then, despite not being formally introduced in any papers.

The same revision cites Gao et al., *A framework for few-shot language model evaluation*, September 2021, <https://doi.org/10.5281/zenodo.5371628>. Figure 2 of that revision describes what a task packages: "1) a data source […] 2) tools for defining prompts and format, 3) mapping these prompts to rendered inputs and expected output type from an LM in the form of Requests, and 4) rules for post-processing the LM's outputs and calculating the final task metrics."

The page [arXiv:2405.14782](https://arxiv.org/abs/2405.14782), read afterwards, is a later revision of the same preprint. It calls the library an orchestrator:

> It is an orchestration library: its role is to make it possible to run any benchmark on any model reliably and reproducibly, not to dictate which benchmarks matter.

The components listed by the authors are those of a benchmark runner: task, prompt, request to the model, post-processing, metric.

## The SWE-bench repository

The repository [princeton-nlp/SWE-bench](https://github.com/princeton-nlp/SWE-bench/) uses *evaluation harness* for the runner that executes a prediction. The [CHANGELOG](https://github.com/princeton-nlp/SWE-bench/blob/main/CHANGELOG.md), entry `[2.0.0]` of 2024-06-27:

> the SWE-bench evaluation harness has been upgraded to incorporate containerized, sandboxed execution environments based on Docker.

The README of the same repository, *Result Caching* note:

> The evaluation harness caches results by `run_id` and `instance_id` only.

The docstring of `main` in [`swebench/harness/run_evaluation.py`](https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/run_evaluation.py): "Run evaluation harness for the given dataset and predictions."

The authors' paper, [arXiv:2310.06770](https://arxiv.org/html/2310.06770), *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?*, read in HTML, does not contain the word *harness*. It presents SWE-bench as "an evaluation framework" and "a robust framework for execution-based evaluation": a model produces a patch, and the revised repository is graded by the software repository's tests. The word *harness* arrives in the repository, with the 2024 Docker runner. This runner receives a patch and runs the tests.

## HELM and Inspect

The HELM paper, [arXiv:2211.09110](https://arxiv.org/abs/2211.09110), *Holistic Evaluation of Language Models* (Liang et al.), cites "Eleuther AI LM Harness (Gao et al., 2021b)" and "the EleutherAI Language Model Evaluation Harness" as an earlier collection of datasets. HELM presents itself there as the holistic evaluation and "a general modular toolkit". The README of the repository [stanford-crfm/helm](https://github.com/stanford-crfm/helm/) names it "an open source Python framework". The verbal occurrence "we harness our evaluation's coverage" in the paper is ordinary English. HELM does not define its runner as a harness.

A search `site:inspect.aisi.org.uk harness` returns two uses of the word, both at Inspect, neither as a definition of Inspect. The [Perplexity](https://inspect.aisi.org.uk/perplexity.html) page refers to the "`loglikelihood` evaluation pattern in the EleutherAI lm-evaluation-harness". The [Evals](https://inspect.aisi.org.uk/evals/index.html.md) catalog names a benchmark *Harness-Bench*. The [Inspect](https://inspect.aisi.org.uk/) home page defines itself as "a framework for frontier AI evaluations" and describes an evaluation as a *Task* made of a dataset, a solver, and a scorer. The [Using Agents](https://inspect.aisi.org.uk/agents.html) page describes agents, solvers, and tools. These pages do not present Inspect as a harness.

## Anthropic: the agent harness

Anthropic uses the word in several texts. The boundary changes with the page. The lists below are those of the cited text.

### April 18, 2025: a script around `claude -p`

[*Claude Code: Best practices for agentic coding*](https://www.anthropic.com/research/claude-code-best-practices) (Boris Cherny, April 18, 2025), section "Use headless mode with a custom harness":

> `claude -p` (headless mode) integrates Claude Code programmatically into larger workflows while leveraging its built-in tools and system prompt.

The example is a loop written by the caller: `claude -p "…" --allowedTools Edit Bash(git commit:*)`. Here the *custom harness* is this script, which launches Claude Code, gives it a task and a list of tools, and reads `OK` or `FAIL`.

### November 26, 2025: the Claude Agent SDK

[*Effective harnesses for long-running agents*](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) (Justin Young, November 26, 2025):

> The Claude Agent SDK is a powerful, general-purpose agent harness adept at coding, as well as other tasks that require the model to use tools to gather context, plan, and execute. It has context management capabilities such as compaction.

Footnote 1 separates three things in the comparison of the experiment's two agents:

> We refer to these as separate agents in this context only because they have different initial user prompts. The system prompt, set of tools, and overall agent harness was otherwise identical.

The body of the article then describes the environment that this experiment adds to span several context windows: a feature list in JSON, a git repository, a `claude-progress.txt` file, an `init.sh` script, test tools (Puppeteer MCP). The *Future work* section calls this set "one possible set of solutions in a long-running agent harness".

### January 9, 2026: the two definitions, and the scaffold

[*Demystifying evals for AI agents*](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) (January 9, 2026) sets the two meanings in the same list:

> An evaluation harness is the infrastructure that runs evals end-to-end. It provides instructions and tools, runs tasks concurrently, records all the steps, grades outputs, and aggregates results.

> An agent harness (or scaffold) is the system that enables a model to act as an agent: it processes inputs, orchestrates tool calls, and returns results. When we evaluate "an agent," we're evaluating the harness and the model working together. For example, Claude Code is a flexible agent harness, and we used its core primitives through the Agent SDK to build our long-running agent harness.

The components of the *agent harness*, in this sentence, are: process inputs, orchestrate tool calls, return results. The parenthetical "or scaffold" makes the scaffold the same object, on this page. Further on, the page speaks of a scaffold again about CORE-Bench: "After fixing bugs and using a less constrained scaffold, Opus 4.5's score jumped to 95%." And: "If […] the harness constrains the model, the eval should be revised."

The *evaluation harness* of the same page provides instructions and tools, runs the tasks in parallel, records the steps, grades, aggregates. It is the runner. The *agent harness* is what this runner measures, together with the model.

### Claude Code documentation

The glossary, [Agentic harness](https://code.claude.com/docs/en/glossary), page consulted without date:

> The tools, context management, and execution environment that turn a language model into a capable coding agent. Claude Code is the harness; Claude is the model inside it. The harness supplies file access, shell execution, permission gating, memory loading, and the loop that chains actions together.

The page [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works), consulted without date, ties the word to the layer around the model:

> The agentic loop is powered by two components: models that reason and tools that act. Claude Code is the layer around the model that provides the tools and manages the context the model sees. This surrounding layer is what the term agentic harness refers to.

The loop has three phases there, "gather context, take action, and verify results". The built-in tools are grouped there into file operations, search, execution, web, and code intelligence. The page also describes the permissions (Auto, Manual, Accept edits, Plan modes) and the checkpoints. These elements are those of this product page. The glossary, for its part, stops at the quoted sentence.

### March 24, 2026: an architecture on the harness already described

[*Harness design for long-running application development*](https://www.anthropic.com/research/harness-design-long-running-apps) (March 24, 2026) takes up "our earlier harness work" and "the original harness". It describes a three-agent architecture, planner, generator, and evaluator, with a sprint contract written in files and an evaluator that clicks through the application via the Playwright MCP. The page does not give a definition of the word again. It adds agent roles to the November 26, 2025 experiment.

### April 8, 2026: the loop, apart from the session and the sandbox

[*Scaling Managed Agents: Decoupling the brain from the hands*](https://www.anthropic.com/research/managed-agents) (Lance Martin, Gabe Cemaj, Michael Cohen, April 8, 2026) virtualizes three components:

> a session (the append-only log of everything that happened), a harness (the loop that calls Claude and routes Claude's tool calls to the relevant infrastructure), and a sandbox (an execution environment where Claude can run code and edit files).

On this page, the harness is the loop. The session and the sandbox are the two other interfaces. The harness calls the container like a tool, `execute(name, input) → string`. It writes into the session through `emitEvent`, reads it back through `getSession` / `getEvents`, and can transform the events before passing them to the context window. "The harness is never made aware of any credentials." The conclusion calls Managed Agents a meta-harness:

> Managed Agents is a meta-harness in the same spirit, unopinionated about the specific harness that Claude will need in the future. […] For example, Claude Code is an excellent harness that we use widely across tasks.

The April 2026 boundary (loop, session, and sandbox separate) and the boundary of the Claude Code glossary (the execution environment included in the harness) are two Anthropic pages. Each keeps its own list.

## OpenAI

OpenAI uses the word in engineering posts, a cookbook, and the Agents SDK announcement. The lists remain those of each page.

### January 23, 2026: the loop

[*Unrolling the Codex agent loop*](https://openai.com/index/unrolling-the-codex-agent-loop/) (January 23, 2026):

> We hope this post gives you a good view into the role our agent (or "harness") plays in making use of an LLM.

> This post focuses on the Codex harness, which provides the core agent loop and execution logic that underlies all Codex experiences and is surfaced through the Codex CLI.

The loop, in this post: the harness takes the user's input, queries the model, and, if the model asks for a tool, executes the call, appends its output to the prompt, and queries the model again.

> This process repeats until the model stops emitting tool calls and instead produces a message for the user.

The stop condition written here is that one: no more tool calls, an assistant message.

### February 4, 2026: what the harness contains beyond the loop

[*Unlocking the Codex harness: how we built the App Server*](https://openai.com/index/unlocking-the-codex-harness/) (Celia Chen, February 4, 2026):

> they're all powered by the same Codex harness, the agent loop and logic that underlies all Codex experiences.

Then, "there's more to the full agent experience":

1. "Thread lifecycle and persistence." Create, resume, fork, archive a thread, persist the history.
2. "Config and auth." Configuration, defaults, authentication, credential state.
3. "Tool execution and extensions. Codex executes shell/file tools in a sandbox and wires up integrations like MCP servers and skills so they can participate in the agent loop under a consistent policy model."

The post adds that the App Server can ask the client for approval and pause the turn until `allow` or `deny`.

### February 11, 2026: *harness engineering*

[*Harness engineering: leveraging Codex in an agent-first world*](https://openai.com/index/harness-engineering/) (Ryan Lopopolo, February 11, 2026) titles a practice: designing the environment in which Codex agents write a product. The post says that the environment lacked tools, abstractions, and structure, and that the team's work became making a capability "both legible and enforceable for the agent". It calls *initial scaffold* the repository structure, CI, formatting, package manager, and framework, generated by Codex. It lists *Evaluation harnesses* among what the agents produce (code, tests, CI, documentation, review comments, dashboards). This post gives no sentence of the type "a harness is …". The *scaffold* of this page is the initial structure of the repository.

### Agents SDK cookbook

The notebook [Build an Agent Improvement Loop with Traces, Evals, and Codex](https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop), page consulted without date, sets a definition for this notebook:

> In this notebook, the harness is the full contract around the model, including instructions, tools, routing, output requirements, and validation checks.

Further on: "Together, the prompt, tools, routing rules, output requirements, and validation checks form the current agent harness." The same page says that the Agents SDK provides "the managed runner, sandbox execution, model settings, and tracing hooks".

### April 15, 2026: the Agents SDK harness

[*The next evolution of the Agents SDK*](https://openai.com/index/the-next-evolution-of-the-agents-sdk/) (April 15, 2026) announces "a model-native harness that lets agents work across files and tools on a computer, plus native sandbox execution". The named primitives:

> configurable memory, sandbox-aware orchestration, Codex-like filesystem tools […] tool use via MCP, progressive disclosure via skills, custom instructions via AGENTS.md, code execution using the shell tool, file edits using the apply patch tool.

The *Separating harness from compute* section separates the harness from the environment where the code produced by the model runs, for credentials, recovery after the loss of a container, and the number of sandboxes.

### *Codex as a platform*

[*Codex as a platform: build on the open agent harness*](https://developers.openai.com/blog/codex-as-a-platform), page consulted without a displayed date:

> A capable agent is more than a prompt and a model response. It needs a way to understand a task, maintain context over time, inspect relevant information, call tools, expose progress, handle failures, request human approval when necessary, and return a useful result.
>
> That surrounding execution system is the harness.

The next sentence on what "we built" lists: conversation state, streamed execution, tools, sandbox and approval policies, work carried over from one turn to the next. The caption of figure 1 says that the application owns "product context, business rules, and tools" and that the app-server provides "the agent loop and sandboxed execution". Further on: "The harness handles the agent loop, conversation state, streamed activity, and tool interaction." The page also gives a figure on the effect of the harness, to be taken as its own: "on ARC-AGI-3, retained reasoning and context compaction raised GPT-5.6 Sol's score from 13.3% to 38.3% while reducing output tokens sixfold."

## LangChain: two objects in the same documentation

The page [Agents](https://docs.langchain.com/oss/python/langchain/agents), consulted without date, defines the harness as the surroundings of the loop, and the agent as the pair:

> An agent is a model calling tools in a loop until a given task is complete.
>
> A harness is everything around that loop: the prompt, the tools, and any middleware that shapes the model's behavior.
>
> Agent = Model + Harness
>
> The job of a harness: get the model the right context at the right time for the given task.

`create_agent` is "a highly configurable harness" there. The page says that middleware extends this harness, and groups this middleware into execution environment (tools, filesystem, sandboxes, code execution), context management (summarization, memory, skills, prompt cache), planning and delegation (task lists, sub-agents), fault tolerance (retries, fallbacks, call limits), guardrails (PII detection, content checks), and steering (human approval).

The page [Runtimes, frameworks, and harnesses](https://docs.langchain.com/oss/javascript/concepts/products), consulted without date, reserves the word for a third tier, next to the runtime and the framework:

> Agent harnesses are opinionated, batteries-included frameworks with built-in tools and capabilities for building sophisticated, long-running agents.

The named capabilities: planning (task list), delegation (sub-agents), filesystem, token management (summarization of the history and eviction of large tool results). The examples given: Deep Agents SDK, Claude Agent SDK, Manus, "and other coding CLIs". On this page, LangChain is the framework ("abstractions like structured content blocks, the agent loop, and middleware") and LangGraph the runtime. OpenAI's Agents SDK is listed there among the frameworks.

The page [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview), consulted without date, calls Deep Agents "an agent harness" and lists four cards: execution environment (tools, virtual filesystem, optional sandbox, interpreter), context management (skills, memory, summarization, offloading, prompt cache), delegation (sub-agents, optional planning), steering (approval and interrupts).

The *Agents* page and the *Products* page therefore use the same word for two different cuts. The first includes `create_agent`. The second leaves it to the framework and keeps *harness* for the already-filled frame.

## Cursor

Cursor uses the word for two objects.

The page [Agent SDK](https://cursor.com/docs/agent-sdk), consulted without date:

> It uses the Cursor SDK harness that powers Bugbot, Security Review, Approval Bot, and Rollouts.

The same page: "You write the instructions and tools, and Cursor runs the model loop that calls them." The page [Agent config](https://cursor.com/docs/agent-sdk/reference/agent-config) titles a section *Harness tools*: the `tools` list limits the built-in tools that the model can call (`shell`, `read`, `edit`, `grep`, `glob`, `ls`, `task`). The [Notion](https://cursor.com/docs/integrations/notion) integration speaks of the "standard Cursor harness" and writes: "every agent uses the same runtime, harness, and models that power Cursor." The Origin CLI, page [Commands](https://cursor.com/docs/origin/cli/reference/commands), has a `--harness` option whose values are `cursor`, `claude-code`, or `codex`.

The page [Agent](https://cursor.com/docs/agent/overview), consulted without date, describes the product without this word. An agent there is three components: instructions, tools, model. "Cursor's agent orchestrates these components for each model we support."

The page [Run Cursor in your evals](https://cursor.com/docs/evals), consulted without date, uses *eval harness* for the evaluator's runner:

> Use the Cursor SDK to run Cursor's agent loop inside your own eval harness.

> Eval harnesses need a stable, programmatic interface to an agent: pass a task, get a transcript and final state, score the result.

The same page says that the loop, the tool schema, the prompts, and the shape of the stream stay constant from one model to the next, "so you measure model-level differences instead of harness drift". The SDK there is the means of running the product's loop (tool calls, edits, commands, reasoning) in this runner.

The TypeScript SDK, page [Cursor TypeScript SDK](https://cursor.com/docs/sdk/typescript), separates the place of the loop from the place of the model: ""Local" describes where the agent loop and filesystem access run, not where the model runs. All inference goes through Cursor's hosted models in both modes."

## How the meanings touch

The evaluation runner is the 2021 meaning: EleutherAI runs a task, a prompt, a request, a score. The SWE-bench repository, in 2024, calls *evaluation harness* the Docker runner that applies a patch and runs tests. Anthropic, on January 9, 2026, restates this meaning: the *evaluation harness* launches the trials, records, grades, aggregates. Cursor, page *Run Cursor in your evals*, describes the same role on the caller's side: pass a task, get back a transcript and a state, grade. Another Cursor page, *Agent SDK*, names the product's loop and tools *Cursor SDK harness*.

The agent harness is the 2025 to 2026 meaning: the software that makes the model act. Anthropic, on January 9, 2026, defines it by the inputs, the orchestration of tools, and the return of results, and evaluates "the harness and the model" together. The Claude Code documentation details it as tools, context, environment, files, shell, permissions, memory, and loop. OpenAI, on January 23 and February 4, 2026, sets it as the Codex loop and, around it, threads, configuration, authentication, sandboxed tools, MCP, skills, approval. The OpenAI cookbook sets it as the instructions / tools / routing / output / validation contract. LangChain, *Agents* page, sets it as the prompt, tools, and middleware around the loop, with the formula Agent = Model + Harness.

The scaffold, as a synonym, is Anthropic's parenthetical of January 9, 2026 ("agent harness (or scaffold)"), taken up again for CORE-Bench ("a less constrained scaffold"). The *custom harness* of April 18, 2025 is the script that calls `claude -p`. The *scaffold* of the OpenAI post of February 11, 2026 is the initial structure of the repository. Three uses, three pages.

The tool loop is, depending on the page, the harness or a piece of the harness. OpenAI, on January 23, 2026, calls it the core of the harness, with the stop when the model no longer emits a tool call. Anthropic, on April 8, 2026, defines the harness as this loop, and puts the session and the sandbox beside it. The Claude Code glossary puts the loop in the list of what the harness supplies. LangChain makes the loop the definition of the agent, and the harness everything around it.

## In this repository

`docs/notes/verification/role-llm-local-harness-multi-machines.md` calls the `agent` service of this stack the harness: bounded loop, named tools, code context, guardrails. This meaning is that of the software around the model. The audit of this service is in that note.
