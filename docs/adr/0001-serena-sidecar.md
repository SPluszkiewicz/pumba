# Serena as the code-intelligence sidecar

The agent talks to Serena over MCP inside Compose. Serena owns clangd, file
read/search, and symbol-level edits on a read-write checkout. The agent does
not import Serena; the editor does not assemble MCP servers. Shell, Python
REPL, JetBrains tools, and disk memories stay off. CMake/ctest stay in `tools`.
Git/svn stay read-only wrappers. The index stays Qdrant.

We picked this over importing Serena (same release cycle as the loop) and over
`.cursor/mcp.json` on the host (the product is `/ui`, not Cursor). Writes
through Serena still open **editing** and still require `build_step`. They
never commit.
