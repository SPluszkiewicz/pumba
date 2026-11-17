# Local harnesses that already exist

> **Status**: informational, no decision. External inventory (product pages read on 2026-11-10); the only repository-specific fact is the 2026-11-06 decision in `docs/notes/verification/external-libraries.md` that rules out OpenHands, Aider and Continue. Product descriptions may have changed since. · **Last verified**: 2026-11-12

**Verdict:** code agents already run as processes on the machine (CLI, extension, desktop application) and document a connection to Ollama, LM Studio, llama.cpp or an OpenAI-compatible base. The word *harness* is rare on these pages: CheetahClaws calls itself that, Open Interpreter makes it a command, gptme uses it for a shared memory. The local loop, the local model and the word remain three distinct facts.

Research note of 2026-11-10.

The meaning of the word is in [`notion-ai-harness.md`](notion-ai-harness.md). This note is the list of products whose loop runs on the machine.

## Question

Which agent harnesses already run on the machine (local process, not a cloud service that owns the loop), and which can point to a local model (OpenAI-compatible API, Ollama, llama.cpp, or an equivalent documented by the project)? For each: does its page use the word harness, which license, is there a shell, and since when does the repository or the README date the project?

Three facts, kept apart: the loop runs on the machine; the model runs on the machine; the project uses the word harness to describe itself.

## Short answer

The "created" dates are those of the repository's GitHub API, read on 2026-11-10. When the README gives another date, the section cites it.

| Project | Name on its page | Loop on the machine | Documented local model | Word harness | Shell | License | Date |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Aider | pair programming in the terminal | `aider` CLI | Ollama (`OLLAMA_API_BASE`) and `OPENAI_API_BASE` | absent from README | `/run`, `/test` | Apache-2.0 | created 2023-05-09 |
| OpenHands | Agent Canvas, self-hosted control center | `agent-canvas` locally | LM Studio, Ollama, vLLM, SGLang, Base URL field | absent from README | `BashTool` in the SDK | MIT | created 2024-03-13 |
| Continue | coding agent | CLI, VS Code, JetBrains | `provider: ollama`, `apiBase` | absent from README | `run_terminal_command` | Apache 2.0 | created 2023-05-24; README: no longer maintained |
| Cline | coding agent | CLI, desktop, VS Code, JetBrains | Ollama, LM Studio, OpenAI-compatible API | absent from README | bash commands in the terminal | Apache 2.0 | created 2024-07-06 |
| Goose | AI agent on the machine | application, CLI, API | Ollama provider, `OLLAMA_HOST` | absent from README | Developer extension, `GOOSE_SHELL` | Apache-2.0 | created 2024-08-23 |
| OpenCode | open source coding agent | TUI, desktop application | Ollama, LM Studio, vLLM, `settings.baseURL` | absent from README | bash commands | MIT | created 2025-04-30 |
| SWE-agent | agent that fixes repositories | `sweagent` command | `agent.model.api_base`, Ollama example | absent from README | `bash` tool | MIT | created 2024-04-02 |
| mini-swe-agent | minimal agent | `mini` CLI | `api_base`, local vLLM example | absent from README | bash, only tool | MIT | created 2025-06-28 |
| Crush | coding bestie in the terminal | `crush` CLI | `ollama`, `llamacpp`, `lmstudio`, `--base-url` | absent from README | `bash` tool | FSL-1.1-MIT | created 2025-05-21 |
| Roo Code | dev team in the editor | extension | Ollama, LM Studio, Base URL setting | absent from README | the pages read do not say | Apache 2.0 | created 2024-10-31; README: extension shut down on 15 May |
| Kilo Code | coding agent | VS Code, JetBrains, CLI | Ollama provider, local base URL | absent from README | terminal | MIT | created 2025-03-10 |
| gptme | personal AI agent | CLI, and editors via ACP | `OPENAI_BASE_URL`, Ollama, LM Studio, vLLM, llama.cpp | "other harness", cross-harness memory | `shell` tool | MIT | README: Spring 2023; created 2023-03-24 |
| Open Interpreter | coding agent | `interpreter` CLI | OpenAI-compatible provider, `--chat-completions` | `/harness` command | commands in a sandbox | Apache-2.0 | created 2023-07-14 |
| CheetahClaws | Agent Harness | `cheetahclaws` CLI | `ollama/…`, `lmstudio/…`, `CUSTOM_BASE_URL` | calls itself that | `!cmd`, bash | Apache-2.0 | created 2026-04-01 |
| Claude Code | vendor's local binary | `claude` command | the installation page read describes no local model | see the note on the word | Bash tool named on the installation page | none | one line, below |
| Codex CLI | vendor's local binary | `codex` command | the page read shows a vendor model | the CLI page read does not use the word | "run commands" | none | one line, below |

## Aider

The README of [Aider-AI/aider](https://github.com/Aider-AI/aider) titles the project this way:

> AI Pair Programming in Your Terminal

> Aider lets you pair program with LLMs to start a new project or build on your existing codebase.

The same page: "can connect to almost any LLM, including local models." The word harness does not appear there. The repository's GitHub API reports the Apache-2.0 license and `created_at` `2023-05-09`.

The page [Connecting to LLMs](https://aider.chat/docs/llms.html): "Aider can work also with local models, for example using Ollama. It can also access local models that provide an Open AI compatible API." The [Ollama](https://aider.chat/docs/llms/ollama.html) page names `OLLAMA_API_BASE` (example `http://127.0.0.1:11434`) and the command `aider --model ollama_chat/<model>`. The [OpenAI compatible APIs](https://aider.chat/docs/llms/openai-compat.html) page names `OPENAI_API_BASE` and `aider --model openai/<model-name>`.

The [In-chat commands](https://aider.chat/docs/usage/commands.html) page describes `/run`: "Run a shell command and optionally add the output to the chat", and `/test`: "Run a shell command and add the output to the chat on non-zero exit code". These are commands typed in the chat.

## OpenHands

The README of [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) presents Agent Canvas:

> The self-hosted developer control center for coding agents and automations.

> It runs locally on your machine by default, but can connect to multiple "agent backends" […] You can optionally choose to run agents on OpenHands Cloud or OpenHands Enterprise infrastructure.

Local launch is `npm install -g @openhands/agent-canvas` then `agent-canvas`. The README also says "Bring your own model" and "Use with any LLM". The word harness does not appear there. The GitHub API reports MIT and `created_at` `2024-03-13`.

The page [Run Local LLMs with OpenHands](https://docs.openhands.dev/openhands/usage/llms/local-llms) separates the model server from the Canvas backend. It documents LM Studio, Ollama, Atomic Chat, SGLang and vLLM. The fields named are Custom Model, Base URL and API Key. For Ollama, the model example is `openai/qwen3.6:35b-a3b` and the Base URL port is `11434`.

The shell is in the SDK docs, the repository the README designates as owner of the tools. [Hello World](https://docs.openhands.dev/sdk/guides/hello-world): "The default agent includes `BashTool`, `FileEditorTool`, etc." [Getting Started](https://docs.openhands.dev/sdk/getting-started): "Agents can execute bash commands, edit files, browse the web, and more."

## Continue

The README of [continuedev/continue](https://github.com/continuedev/continue) says, about itself:

> The `continuedev/continue` repository is no longer actively maintained and is read-only for all users.

> Continue is a coding agent available as a CLI, VS Code extension, and JetBrains plugin.

The same page announces a "Final 2.0.0 Release" and closes with "Apache 2.0 © 2023-2026 Continue Dev, Inc." The word harness does not appear there. The GitHub API reports `created_at` `2023-05-24` and, the same day, `"archived": false`.

The [Ollama](https://docs.continue.dev/customize/model-providers/top-level/ollama) page names `provider: ollama` and `apiBase` (example `http://<my endpoint>:11434`). The [How Agent Mode Works](https://docs.continue.dev/ide-extensions/agent/how-it-works) page names the `run_terminal_command` tool: "Run commands from the workspace root." The model chooses the call; Continue runs it after permission, unless the policy is `Automatic`.

## Cline

The README of [cline/cline](https://github.com/cline/cline):

> The open source coding agent in your IDE, terminal, & desktop!

The surfaces listed: CLI (`npm i -g cline`), desktop application, VS Code extension, JetBrains plugin. The word harness does not appear there. The license written at the bottom: "Apache 2.0 © 2026 Cline Bot Inc." The GitHub API reports `created_at` `2024-07-06`.

The "Runs Bash Commands" section:

> Cline executes commands directly in your terminal and watches the output in real time.

The "Works With Every Model" table contains the row "Ollama / LM Studio | Run local models on your machine" and the row "Any OpenAI-compatible API | Self-hosted or third-party endpoints". The [Local models](https://github.com/cline/cline/blob/main/docs/running-models-locally/overview.mdx) page names the Ollama provider and the Base URL `http://localhost:11434`, as well as LM Studio.

## Goose

The GitHub API for `block/goose` answers with the repository [aaif-goose/goose](https://github.com/aaif-goose/goose). The README:

> goose is a general-purpose AI agent that runs on your machine.

> A native desktop app for macOS, Linux, and Windows. A full CLI for terminal workflows. An API to embed it anywhere.

> goose works with 15+ providers. Anthropic, OpenAI, Google, Ollama, OpenRouter, Azure, Bedrock, and more.

The word harness does not appear there. The API reports Apache-2.0 and `created_at` `2024-08-23`. The same page says that goose is part of the Agentic AI Foundation at the Linux Foundation.

The [providers](https://github.com/aaif-goose/goose/blob/main/documentation/docs/getting-started/providers.md) doc describes the Ollama provider: "Because this provider runs locally, you must first download and run a model." The variable named is `OLLAMA_HOST`. With no host provided, the doc sets `localhost:11434`. The [Using Extensions](https://goose-docs.ai/docs/getting-started/using-extensions.md) page: "goose can execute commands and modify files". The [Environment Variables](https://goose-docs.ai/docs/guides/environment-variables.md) page names `GOOSE_SHELL`: "Overrides the shell used for Developer extension shell commands".

## OpenCode

Querying the GitHub API for `sst/opencode` returns [anomalyco/opencode](https://github.com/anomalyco/opencode), same repository id. The README of the default branch (`dev`) and the installation lines (`nix run github:anomalyco/opencode`, `brew install anomalyco/tap/opencode-v2`) carry this repository. The published doc is [opencode.ai](https://opencode.ai/docs/).

The README:

> The open source AI coding agent.

It describes a TUI and a desktop application. The `plan` agent "Asks permission before running bash commands". The word harness does not appear there. The API reports MIT and `created_at` `2025-04-30`.

The [Providers](https://opencode.ai/docs/providers/) page: OpenCode probes `http://127.0.0.1:11434` for Ollama, and the setting for a remote server is `settings.baseURL`. The Runtimes section gives LM Studio (`http://127.0.0.1:1234/v1`) and vLLM (`http://127.0.0.1:8000/v1`), both under `settings.baseURL`. For another OpenAI-compatible runtime, the page refers to the custom provider recipe. This published page does not name llama.cpp.

## SWE-agent

The README of [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent):

> SWE-agent enables your language model of choice (e.g. GPT-4o or Claude Sonnet 4) to autonomously use tools to fix issues in real GitHub repositories […]

The same README directs development toward mini-swe-agent, "which has superseded SWE-agent". The word harness does not appear there. The license written: "MIT. Check `LICENSE`." The API reports `created_at` `2024-04-02`. The README's citation dates the paper to 2024.

The [Models and keys](https://swe-agent.com/latest/installation/keys/) page: "We currently support all models that serve to an endpoint with an OpenAI-compatible API." The YAML example names `agent.model.name: ollama/llama2` and `api_base: http://localhost:11434`. The [Tools](https://swe-agent.com/latest/config/tools/) page: "The `bash` tool, allowing the agent to run shell commands (including invoking python scripts)". This page adds that SWE-agent is in maintenance, current development being mini-swe-agent.

## mini-swe-agent

The README of [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent), which the SWE-agent README designates as the default choice:

> The minimal AI software engineering agent

> Supports **local environments**, **docker/podman**, **singularity/apptainer**, **bublewrap**, **contree**, and more

> Does not have any tools other than bash

The CLI is installed with `pip install mini-swe-agent` then `mini`. The word harness does not appear there. The GitHub API reports MIT and `created_at` `2025-06-28`. The README dates SWE-agent and SWE-bench to 2024, and this repository as the follow-up.

The [Local models](https://mini-swe-agent.com/latest/models/local_models/) page goes through LiteLLM. It names `model_kwargs`, `custom_llm_provider` and `api_base`. The vLLM example sets `api_base: "http://localhost:8000/v1"`. The example cost registry contains an entry `"litellm_provider": "ollama"`.

## Crush

The README of [charmbracelet/crush](https://github.com/charmbracelet/crush):

> Your new coding bestie, now available in your favourite terminal.

The repository's GitHub description: "Glamourous agentic coding for all". The word harness does not appear in this README. The README's License section: "FSL-1.1-MIT". The [LICENSE.md](https://github.com/charmbracelet/crush/blob/main/LICENSE.md) file is titled "Functional Source License, Version 1.1, MIT Future License" and carries "Copyright 2025-2026 Charmbracelet, Inc." The GitHub API classifies the license as `NOASSERTION` and gives `created_at` `2025-05-21`.

The README's Local Models section: a provider of type `llamacpp`, `omlx`, `lmstudio`, `litellm` or `ollama`, with `--base-url`. The Ollama example is `http://localhost:11434/v1/`, the llama.cpp example `http://localhost:2222`. The README names the `bash` tool: `permissions deny bash`, and "Crush will ask you for permission before running tool calls".

## Roo Code

The README of [RooCodeInc/Roo-Code](https://github.com/RooCodeInc/Roo-Code):

> Your AI-Powered Dev Team, Right in Your Editor

Then:

> The Roo Code Extension was shut down on May 15th.

The word harness does not appear there. The license written: "Apache 2.0 © 2026 Roo Code, Inc." The GitHub API reports `created_at` `2024-10-31`, `"archived": true`, and a last push on `2026-05-15`. The README does not give the year of May 15.

The [Using Local Models](https://github.com/RooCodeInc/Roo-Code-Docs/blob/main/docs/advanced-usage/local-models.md) page: "Roo Code supports running language models locally on your own machine using Ollama and LM Studio." The troubleshooting names the Base URL setting. Neither this README nor this page mentions a shell.

## Kilo Code

The README of [Kilo-Org/kilocode](https://github.com/Kilo-Org/kilocode):

> The open source coding agent for building with AI in VS Code, JetBrains, or the CLI.

The CLI is `kilo`. The same page also describes a Cloud Agent: "Run Kilo from the web, no local machine needed". The word harness does not appear there. The license written: "MIT." The API reports `created_at` `2025-03-10`. The README's FAQ: "Kilo CLI is a fork of OpenCode".

"What it does": "Terminal and browser control to run commands and automate the web." The [Using Ollama with Kilo Code](https://kilo.ai/docs/ai-providers/ollama) page: the Ollama provider targets a local `ollama serve` daemon, local models have the form `ollama/<model_name>`, and "connect to your local base URL". It distinguishes Ollama Cloud, which does not connect to `http://localhost:11434`. The file named for the config is `kilo.json`.

## gptme

The README of [gptme/gptme](https://github.com/gptme/gptme):

> A personal AI agent that runs anywhere a terminal runs, your laptop, ssh sessions, tmux, headless servers, CI pipelines.

> fully local via Ollama and any OpenAI-compatible server

> one of the first agent CLIs (Spring 2023)

The GitHub API reports MIT and `created_at` `2023-03-24`. The word harness there designates a class of products, in the shared memory:

> One local Markdown-based memory store shared by gptme, Claude Code, Codex, and any other harness.

The tools table names `shell`: "Execute shell commands directly in your terminal". The local example:

> `OPENAI_BASE_URL="http://127.0.0.1:11434/v1" gptme 'hello' -m local/llama3.2:3b`

The same page lists Ollama, LM Studio, vLLM and `llama.cpp`.

## Open Interpreter

The README of [openinterpreter/openinterpreter](https://github.com/openinterpreter/openinterpreter):

> A coding agent optimized for low-cost models.

> Open Interpreter is a fork of OpenAI's Codex, with a focus on emulating the agent harness that gets the best performance out of low-cost models.

> Use `/harness` to switch the active harness

The list displayed by `/harness` includes `native`, `claude-code`, `swe-agent`, `minimal`, and others. The license written: "Apache-2.0". The API reports `created_at` `2023-07-14`. The README says that the current tree is "the new Rust version of Open Interpreter, based on Codex", and sends the original Python project to a community fork. This page does not date the Rust rewrite.

The shell: "Runs commands inside native sandboxing on macOS, Linux, and Windows." The model: "Runs any selected OpenAI-compatible provider through Chat Completions with `interpreter --chat-completions`". This page names neither Ollama, nor llama.cpp, nor a base URL field.

## CheetahClaws

The README of [SAIL-Research-Lab/cheetahclaws](https://github.com/SAIL-Research-Lab/cheetahclaws) names itself with the word:

> CheetahClaws: A Fast and Easy-to-Use Python native Agent Harness Infrastructure, Supporting Any Model […] and local open-source models via Ollama or any OpenAI-compatible endpoint.

The repository's GitHub description: "CheetahClaws: Scaling the Harness in Agentic AI". The CLI is `cheetahclaws`. The API reports Apache-2.0 and `created_at` `2026-04-01`. The README's changelog dates versions up to 31 October 2026 (v3.5.90).

The Ollama example: `cheetahclaws --model ollama/qwen2.5-coder`, server `http://localhost:11434`. LM Studio: `cheetahclaws --model lmstudio/<model-name>`. An OpenAI-compatible server: `CUSTOM_BASE_URL` (example `http://localhost:8000/v1`) and `cheetahclaws --model custom/…`. The README describes a `!cmd` shell escape, a `bash_sandbox`, and commands that the turn executes.

## Vendor binaries

Two pages describe a local binary whose model, on the page read, remains the vendor's. They do not enter the sections above.

[Advanced setup](https://code.claude.com/docs/en/setup) installs a `claude` binary (`curl -fsSL https://claude.ai/install.sh | bash`, then `claude`). The page names a Bash tool, and PowerShell as the fallback shell on Windows. It does not describe a local model.

[Codex CLI](https://developers.openai.com/codex/cli) installs a `codex` binary and says "run commands". The same page documents `codex cloud` to send the work to the cloud. The word harness does not appear there. The model shown on the page is a vendor model.

## Pages read and left out of the table

A search for the phrase "agent harness" also returns [ruvnet/metaharness](https://github.com/ruvnet/metaharness), [ACNoonan/relay-cli](https://github.com/ACNoonan/relay-cli) and [0xNyk/lacp](https://github.com/0xNyk/lacp). Their READMEs, read as excerpts, describe a harness factory or a control plane around Claude Code, Codex or Cursor. These excerpts do not document an Ollama setting or a base URL for a loop that would be their own.

## In this repository

`docs/notes/verification/external-libraries.md`, decision of 2026-11-06, rules out OpenHands, Aider and Continue: "Shell and/or cloud LLM; Continue is no longer maintained". The Continue README, read for this note, writes on its side that the repository is no longer maintained.

`docs/notes/verification/role-llm-local-harness-multi-machines.md` calls the `agent` service of this stack a harness: bounded loop, named tools, code context, guardrails.
