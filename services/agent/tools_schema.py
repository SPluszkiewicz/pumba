"""OpenAI-style function-calling schema for the `tools` service.

Nothing is hand-written here any more: the schema, the name -> endpoint map
and the argument models all come from `common/tool_models.py`, the same
module the `tools` service validates its requests with. Add a tool there
(one `ToolSpec` + one pydantic model) and it shows up in both places.

Today that is 23 tools: build, test, compile_file, lint_clang_tidy,
format_check, static_analysis, search, list_files, read_file, git_log,
git_blame, git_diff, git_show, svn_status, svn_diff, svn_log and the seven
clangd tools (lsp_definition, lsp_references, lsp_hover, lsp_symbols,
lsp_outline, lsp_calls, lsp_types). `TOOL_SPECS` in `common/tool_models.py`
is the list; `docs/concepts/tool-calling.md` has the name -> endpoint table.
"""

from __future__ import annotations

from common.tool_models import TOOL_SPECS_BY_NAME, endpoint_map, openai_tools_schema

TOOL_ENDPOINT_MAP: dict[str, dict[str, str]] = endpoint_map()
TOOLS_OPENAI_SCHEMA: list[dict] = openai_tools_schema()
TOOL_NAMES: frozenset[str] = frozenset(TOOL_ENDPOINT_MAP.keys())

__all__ = ["TOOL_ENDPOINT_MAP", "TOOLS_OPENAI_SCHEMA", "TOOL_NAMES", "TOOL_SPECS_BY_NAME"]
