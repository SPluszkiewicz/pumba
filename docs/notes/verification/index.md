# Verification notes

These pages record a question, what was read, and what was checked. They are not the manual. A guide describes what to do. A note here says what was true on the day it was verified.

## Checks you can rerun

- [Functional checks](functional-checks.md). The `deploy.py check` list and the manual MCP campaign on `sample/`.
- [RTX 6000 scenarios](rtx6000-scenarios.md). S0 to S5, one profile at a time.
- [Retrieval baseline](retrieval-baseline.md). Whether the top 5 still find the file on a large checkout.
- [Roadmap](roadmap.md). Improvements, with the check that marked each one done.
- [Agent loop tickets](agent-loop-tickets.md). AL-1 to AL-11.
- [External libraries](external-libraries.md). What was ruled out, and the Serena order.
- [Stack presentation](stack-presentation.md). Inventory of the running stack, and the comparison with OpenCodeReview.

## Research

- [Linux compatibility](linux-compatibility.md). What the repository declares, and what CI actually runs.
- [Indexing a large repository](indexation-gros-depot.md). Time and memory on a tree bigger than `sample/`.
- [Indexer on commit](indexeur-au-commit.md). What the incremental index reads and writes.
- [Settings legibility](settings-comprehensibilite.md). Which `.env` keys the process actually reads.
- [Model for about twenty users on the EVO-X5](evo-x5-modele-20-utilisateurs.md). Memory and slots for one shared model.
- [Qwen and Mistral deploy evaluation](eval-qwen-mistral-deploy.md). What the existing checks can and cannot compare.
- [One stack, several machines](pumba-serveur-multi-clients.md). Loopback, tunnel, and relay.
- [What is missing for parallel clients](clients-paralleles-manquants.md). Accounts, isolation, quotas.
- [Local LLM, harness, several machines](role-llm-local-harness-multi-machines.md). Which of those three roles the code fills.
- [MCP coverage of analyses](mcp-couverture-analyses.md). Which analysis tools this MCP server does not expose.
- [Local harnesses that already exist](harnais-locaux-existants.md). Other products, from their own pages.
- [The notion of AI harness](notion-ai-harness.md). What external pages mean by the word.
- [Cursor and Copilot quotas](cursor-vs-copilot-quotas.md). Published prices, with the page they came from.
