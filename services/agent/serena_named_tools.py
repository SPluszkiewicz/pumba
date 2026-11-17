"""Named tools the model calls on the Serena sidecar.

These are not routes on `tools`. The loop validates arguments
with the same extra-forbid models, then dispatches the call to the sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Type

from common.tool_models import ToolArgs, _simplify
from pydantic import Field


class FindSymbolArgs(ToolArgs):
    name_path_pattern: str = Field(
        description=(
            "Name path of the symbol. A bare name matches any symbol with that name. "
            "A relative path such as 'Class/method' matches that suffix. "
            "A leading slash requires the full name path inside the file."
        )
    )
    depth: int = Field(
        default=0,
        ge=0,
        description="How many levels of child symbols to include. 0 returns only the match.",
    )
    relative_path: str = Field(
        default="",
        description="Optional file or directory that limits the search. Empty searches the whole checkout.",
    )
    include_body: bool = Field(
        default=False,
        description="Include the symbol's source. Leave false unless the body is required.",
    )
    include_info: bool = Field(
        default=False,
        description="Include hover-like type and documentation. Ignored when include_body is true.",
    )
    include_kinds: list[int] = Field(
        default_factory=list,
        description="LSP symbol kind integers to keep. Empty keeps every kind.",
    )
    exclude_kinds: list[int] = Field(
        default_factory=list,
        description="LSP symbol kind integers to drop. These win over include_kinds.",
    )
    substring_matching: bool = Field(
        default=False,
        description="When true, the last element of the pattern matches a prefix of the symbol name.",
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class FindDeclarationArgs(ToolArgs):
    relative_path: str = Field(description="File that contains the symbol, relative to the checkout.")
    regex: str = Field(
        description=(
            "Regular expression with one group. The group matches the symbol to look up. "
            "Include enough surrounding text to make the match unambiguous."
        )
    )
    containing_symbol_name_path: str | None = Field(
        default=None,
        description="Name path of a containing symbol. When set, the search stays inside that symbol's body.",
    )
    include_body: bool = Field(
        default=False,
        description="Include the symbol's source. Leave false unless the body is required.",
    )
    include_info: bool = Field(
        default=False,
        description="Include hover-like type and documentation.",
    )


class FindReferencingSymbolsArgs(ToolArgs):
    name_path: str = Field(description="Name path of the symbol to find references for.")
    relative_path: str = Field(description="File that contains the symbol, relative to the checkout.")
    include_kinds: list[int] = Field(
        default_factory=list,
        description="LSP symbol kind integers to keep. Empty keeps every kind.",
    )
    exclude_kinds: list[int] = Field(
        default_factory=list,
        description="LSP symbol kind integers to drop. These win over include_kinds.",
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class FindImplementationsArgs(ToolArgs):
    name_path: str = Field(description="Name path of the symbol whose implementations to find.")
    relative_path: str = Field(
        description="File that contains the symbol, relative to the checkout. A directory is not accepted."
    )
    include_info: bool = Field(
        default=False,
        description="Include hover-like type and documentation for each implementation.",
    )
    include_kinds: list[int] = Field(
        default_factory=list,
        description="LSP symbol kind integers to keep. Empty keeps every kind.",
    )
    exclude_kinds: list[int] = Field(
        default_factory=list,
        description="LSP symbol kind integers to drop. These win over include_kinds.",
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class GetSymbolsOverviewArgs(ToolArgs):
    relative_path: str = Field(description="File to outline, relative to the checkout.")
    depth: int = Field(
        default=-1,
        description=(
            "How many levels of child symbols to include. -1 uses the language default: "
            "1 for Java and Kotlin, 0 for other languages."
        ),
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class GetDiagnosticsForFileArgs(ToolArgs):
    relative_path: str = Field(description="File to inspect, relative to the checkout.")
    start_line: int = Field(default=0, description="First 0-based line to include.")
    end_line: int = Field(default=-1, description="Last 0-based line to include. -1 means through the end of the file.")
    min_severity: int = Field(
        default=4,
        description="Minimum LSP severity to include: 1=Error, 2=Warning, 3=Information, 4=Hint.",
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class GetDiagnosticsForSymbolArgs(ToolArgs):
    name_path: str = Field(description="Name path of the symbol to inspect.")
    reference_file: str = Field(
        default="",
        description="File that disambiguates the symbol, relative to the checkout. Empty searches by name.",
    )
    check_symbol_references: bool = Field(
        default=False,
        description="Also collect diagnostics for symbols that reference this one.",
    )
    min_severity: int = Field(
        default=4,
        description="Minimum LSP severity to include: 1=Error, 2=Warning, 3=Information, 4=Hint.",
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class SearchForPatternArgs(ToolArgs):
    substring_pattern: str = Field(description="Regular expression to search for.")
    context_lines_before: int = Field(
        default=0,
        description="Number of context lines to include before each match.",
    )
    context_lines_after: int = Field(
        default=0,
        description="Number of context lines to include after each match.",
    )
    paths_include_glob: str = Field(
        default="",
        description=(
            "Optional glob, relative to the checkout, that limits which files are searched. "
            "Empty includes every file the other filters allow."
        ),
    )
    paths_exclude_glob: str = Field(
        default="",
        description="Optional glob of files to exclude. This wins over paths_include_glob.",
    )
    relative_path: str = Field(
        default="",
        description="File or directory that limits the search. Empty searches the whole checkout.",
    )
    restrict_search_to_code_files: bool = Field(
        default=False,
        description="When true, search only files that contain analyzable code symbols.",
    )
    skip_ignored_files: bool = Field(
        default=True,
        description="Whether to skip ignored paths.",
    )
    multiline: bool = Field(
        default=True,
        description="When true, the pattern matches across lines.",
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class ReadFileArgs(ToolArgs):
    relative_path: str = Field(description="The relative path to the file to read.")
    start_line: int = Field(
        default=0,
        description="The 0-based index of the first line to retrieve. Negative values count from the end.",
    )
    end_line: int | None = Field(
        default=None,
        description=(
            "The 0-based index of the last line to retrieve, inclusive. "
            "Empty reads through the end of the file."
        ),
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default. Above the agent cap is refused.",
    )


class FindFileArgs(ToolArgs):
    file_mask: str = Field(description="Filename or mask to search for. The wildcards * and ? are allowed.")
    relative_path: str = Field(
        description="Directory to search, relative to the checkout. Pass '.' to scan the checkout root."
    )
    skip_ignored_files: bool = Field(
        default=False,
        description="Whether to skip ignored files and directories.",
    )


class ListDirArgs(ToolArgs):
    relative_path: str = Field(
        description="Directory to list, relative to the checkout. Pass '.' to scan the checkout root."
    )
    recursive: bool = Field(description="Whether to scan subdirectories.")
    skip_ignored_files: bool = Field(
        default=False,
        description="Whether to skip files and directories that are ignored.",
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class RestartLanguageServerArgs(ToolArgs):
    """No arguments. Serena restarts the language server for the checkout."""


class RenameSymbolArgs(ToolArgs):
    name_path: str = Field(
        description=(
            "Name path of the symbol to rename. For an overload, include the signature that makes the symbol unique."
        )
    )
    relative_path: str = Field(description="File that contains the symbol, relative to the checkout.")
    new_name: str = Field(description="The new name for the symbol.")


class ReplaceSymbolBodyArgs(ToolArgs):
    name_path: str = Field(
        description=(
            "Name path of the symbol whose body to replace. "
            "For an overload, include the signature that makes the symbol unique."
        )
    )
    relative_path: str = Field(description="File that contains the symbol, relative to the checkout.")
    body: str = Field(
        description=(
            "The new symbol definition, including the signature line. Read the current body before replacing it."
        )
    )


class InsertBeforeSymbolArgs(ToolArgs):
    name_path: str = Field(description="Name path of the symbol before which to insert content.")
    relative_path: str = Field(description="File that contains the symbol, relative to the checkout.")
    body: str = Field(description="Content to insert before the line where the symbol is defined.")


class InsertAfterSymbolArgs(ToolArgs):
    name_path: str = Field(description="Name path of the symbol after which to insert content.")
    relative_path: str = Field(description="File that contains the symbol, relative to the checkout.")
    body: str = Field(description="Content to insert, starting on the line after the symbol's definition.")


class SafeDeleteSymbolArgs(ToolArgs):
    name_path_pattern: str = Field(
        description="Name path of the symbol to delete. It is deleted only when nothing references it."
    )
    relative_path: str = Field(description="File that contains the symbol, relative to the checkout.")


class ReplaceContentArgs(ToolArgs):
    relative_path: str = Field(description="File to edit, relative to the checkout.")
    needle: str = Field(
        description=(
            "Text or regular expression to replace. In regex mode, prefer a short pattern "
            "with wildcards over pasting the whole original text."
        )
    )
    repl: str = Field(description="Replacement text. In regex mode, $!1, $!2, and so on refer to capture groups.")
    mode: Literal["literal", "regex"] = Field(
        description="How to read needle: 'literal' for exact text, or 'regex' for a Python regular expression."
    )
    allow_multiple_occurrences: bool = Field(
        default=False,
        description="When false, more than one match is an error and the file is left unchanged.",
    )


class ReplaceInFilesArgs(ToolArgs):
    needle: str = Field(description="Text or regular expression to replace across files.")
    repl: str = Field(description="Replacement text. In regex mode, $!1, $!2, and so on refer to capture groups.")
    mode: Literal["literal", "regex"] = Field(
        description="How to read needle: 'literal' for exact text, or 'regex' for a Python regular expression."
    )
    relative_path: str = Field(
        default="",
        description="File or directory that limits the replacement. Empty considers the whole checkout.",
    )
    paths_include_glob: str = Field(
        default="",
        description=(
            "Optional glob, relative to the checkout, that limits which files are changed. Empty has no extra limit."
        ),
    )
    paths_exclude_glob: str = Field(
        default="",
        description="Optional glob of files to leave unchanged. This wins over paths_include_glob.",
    )
    dry_run: bool = Field(
        default=False,
        description="When true, return the prospective changes and write nothing.",
    )
    occurrence_ids: list[str] | None = Field(
        default=None,
        description="Occurrence ids from a dry run to apply. Omit to replace every match.",
    )
    expected_count: int = Field(
        default=-1,
        description=(
            "When not selecting occurrence ids, the number of matches you expect. "
            "A different count writes nothing. -1 disables the guard."
        ),
    )
    max_answer_chars: int = Field(
        default=-1,
        ge=-1,
        description="Maximum characters in the answer. -1 uses the sidecar default.",
    )


class ReplaceLinesArgs(ToolArgs):
    relative_path: str = Field(description="File to edit, relative to the checkout.")
    start_line: int = Field(description="First 0-based line of the range to replace.")
    end_line: int = Field(description="Last 0-based line of the range to replace.")
    content: str = Field(description="Text that replaces that line range.")


class InsertAtLineArgs(ToolArgs):
    relative_path: str = Field(description="File to edit, relative to the checkout.")
    line: int = Field(description="0-based line index where the content is inserted. Later lines move down.")
    content: str = Field(description="Text to insert at that line.")


class DeleteLinesArgs(ToolArgs):
    relative_path: str = Field(description="File to edit, relative to the checkout.")
    start_line: int = Field(description="First 0-based line to delete.")
    end_line: int = Field(description="Last 0-based line to delete.")


class CreateTextFileArgs(ToolArgs):
    relative_path: str = Field(description="File to create, relative to the checkout.")
    content: str = Field(description="Text to write. An existing file at that path is overwritten.")


@dataclass(frozen=True)
class SerenaNamedTool:
    name: str
    description: str
    model: Type[ToolArgs]
    writes: bool = False


SERENA_NAMED_TOOLS: tuple[SerenaNamedTool, ...] = (
    SerenaNamedTool(
        "find_symbol",
        "Find a symbol by name in the checkout. Use this when you know the name and not a line and column. "
        "The sidecar returns the locations.",
        FindSymbolArgs,
    ),
    SerenaNamedTool(
        "find_declaration",
        "Find the declaration of a symbol in a file. The regular expression has one group around the name.",
        FindDeclarationArgs,
    ),
    SerenaNamedTool(
        "find_referencing_symbols",
        "Find symbols that reference a symbol in a file. The sidecar returns the referencing locations.",
        FindReferencingSymbolsArgs,
    ),
    SerenaNamedTool(
        "find_implementations",
        "Find implementations of a symbol in a file. The sidecar returns the overriding symbols.",
        FindImplementationsArgs,
    ),
    SerenaNamedTool(
        "get_symbols_overview",
        "List the symbols in one file. Use this to understand a file before asking about one name.",
        GetSymbolsOverviewArgs,
    ),
    SerenaNamedTool(
        "get_diagnostics_for_file",
        "Show clangd diagnostics for one file. This does not run a full build.",
        GetDiagnosticsForFileArgs,
    ),
    SerenaNamedTool(
        "get_diagnostics_for_symbol",
        "Show clangd diagnostics for one symbol. This does not run a full build.",
        GetDiagnosticsForSymbolArgs,
    ),
    SerenaNamedTool(
        "list_dir",
        "List files and directories in one checkout directory. Pass '.' for the checkout root.",
        ListDirArgs,
    ),
    SerenaNamedTool(
        "read_file",
        "Read one file in the checkout, optionally a line window. Confirm the text before commenting on it.",
        ReadFileArgs,
    ),
    SerenaNamedTool(
        "find_file",
        "Find files whose names match a mask in one checkout directory. Pass '.' to scan the checkout root.",
        FindFileArgs,
    ),
    SerenaNamedTool(
        "search_for_pattern",
        "Search the checkout for a regular expression and return the matching lines.",
        SearchForPatternArgs,
    ),
    SerenaNamedTool(
        "restart_language_server",
        "Restart the language server after compile_commands.json changes so new files are visible.",
        RestartLanguageServerArgs,
    ),
    SerenaNamedTool(
        "rename_symbol",
        "Rename a symbol across the checkout. This writes the working copy. It does not commit or push.",
        RenameSymbolArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "replace_symbol_body",
        "Replace the full definition of a symbol. This writes the working copy. It does not commit or push.",
        ReplaceSymbolBodyArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "insert_before_symbol",
        "Insert content before a symbol's definition. This writes the working copy. It does not commit or push.",
        InsertBeforeSymbolArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "insert_after_symbol",
        "Insert content after a symbol's definition. This writes the working copy. It does not commit or push.",
        InsertAfterSymbolArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "safe_delete_symbol",
        "Delete a symbol when nothing references it. This writes the working copy. It does not commit or push.",
        SafeDeleteSymbolArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "replace_content",
        "Replace text in one file. This writes the working copy. It does not commit or push.",
        ReplaceContentArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "replace_in_files",
        "Replace a pattern across files in the checkout. This writes the working copy. It does not commit or push.",
        ReplaceInFilesArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "replace_lines",
        "Replace a range of lines in a file. This writes the working copy. It does not commit or push.",
        ReplaceLinesArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "insert_at_line",
        "Insert content at a line in a file. This writes the working copy. It does not commit or push.",
        InsertAtLineArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "delete_lines",
        "Delete a range of lines in a file. This writes the working copy. It does not commit or push.",
        DeleteLinesArgs,
        writes=True,
    ),
    SerenaNamedTool(
        "create_text_file",
        "Create or overwrite a text file in the checkout. This writes the working copy. It does not commit or push.",
        CreateTextFileArgs,
        writes=True,
    ),
)

SERENA_SPECS_BY_NAME: dict[str, SerenaNamedTool] = {tool.name: tool for tool in SERENA_NAMED_TOOLS}
SERENA_TOOL_NAMES = frozenset(SERENA_SPECS_BY_NAME)
SERENA_WRITE_NAMES = frozenset(tool.name for tool in SERENA_NAMED_TOOLS if tool.writes)


def serena_openai_schema() -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in SERENA_NAMED_TOOLS:
        parameters = _simplify(tool.model.model_json_schema())
        parameters.setdefault("required", [])
        tools.append(
            {
                "type": "function",
                "function": {"name": tool.name, "description": tool.description, "parameters": parameters},
            }
        )
    return tools
