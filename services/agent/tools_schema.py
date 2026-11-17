"""OpenAI-style function-calling schema for the `tools` service.

Nothing is hand-written here any more: the schema, the name -> endpoint map
and the argument models all come from `common/tool_models.py`, the same
module the `tools` service validates its requests with. Add a tool there
(one `ToolSpec` + one pydantic model) and it shows up in both places.

`TOOL_SPECS` in `common/tool_models.py` is the HTTP list: build, test,
compile_file, lint, format, cppcheck, git, svn, lsp_calls, lsp_types, and
build_step. Serena names are appended beside that list.

Serena names from `serena_named_tools.py` are appended to that same schema.
They are not routes on `tools`.
"""

from __future__ import annotations

from common.tool_models import TOOL_SPECS_BY_NAME as _HTTP_SPECS_BY_NAME
from common.tool_models import endpoint_map, openai_tools_schema
from serena_named_tools import SERENA_SPECS_BY_NAME, serena_openai_schema

TOOL_ENDPOINT_MAP: dict[str, dict[str, str]] = endpoint_map()
# One schema for /ui and the CLI. Serena names are appended; they are not tools routes.
TOOLS_OPENAI_SCHEMA: list[dict] = [*openai_tools_schema(), *serena_openai_schema()]
TOOL_SPECS_BY_NAME = {**_HTTP_SPECS_BY_NAME, **SERENA_SPECS_BY_NAME}
TOOL_NAMES: frozenset[str] = frozenset(TOOL_ENDPOINT_MAP.keys()) | frozenset(SERENA_SPECS_BY_NAME)

__all__ = ["TOOL_ENDPOINT_MAP", "TOOLS_OPENAI_SCHEMA", "TOOL_NAMES", "TOOL_SPECS_BY_NAME"]
