"""The `tools` service: a FastAPI app exposing a curated, allow-listed set of
build/test/lint/search/read-file endpoints for the C++ project mounted
read-only at `TOOLS_PROJECT_ROOT` (default `/workspace/app`).

See CONTRACTS.md for the contract and docs/notes/contracts/tools-api.md
for the reading note.
"""
