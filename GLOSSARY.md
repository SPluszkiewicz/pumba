# Pumba

A local, offline coding assistant for a C++/CMake tree. The page asks; named tools verify; the model has no shell.

## Language

**Checkout**:
A registered working copy the assistant may see. Unregistered paths are refused.
_Avoid_: workspace, project, repo (when you mean this copy)

**Named tool**:
A bounded action with a name and checked arguments. Not a free command line.
_Avoid_: command, shell, function, MCP tool (when you mean the agent schema)

**Agent**:
The orchestrator that classifies the question, retrieves, and runs the named-tool loop.
_Avoid_: assistant, model, LLM (those are other pieces)

**Editing**:
Turn state after a successful write and before a successful `build_step`.
_Avoid_: campaign, plan (those are how a write starts, not the state)

**Index**:
The catalogue of structurally cut chunks used to retrieve by meaning and by words.
_Avoid_: embeddings, RAG, vector store (storage is not the catalogue)

**Sidecar**:
A process beside the agent, reached over the network, upgraded on its own cycle.
_Avoid_: library, plugin, MCP server on the editor host
