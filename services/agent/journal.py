"""Journal excerpts shown back to the model and stored in the next turn."""

from __future__ import annotations

VERIFY_TOOLS = frozenset(
    {
        "build",
        "build_step",
        "test",
        "compile_file",
        "lint_clang_tidy",
        "format_check",
        "static_analysis",
    }
)


def journal_excerpt(name: str, stderr: str = "", stdout: str = "") -> str:
    """Error tail for a verification tool, otherwise the first non-empty line."""
    if name in VERIFY_TOOLS:
        source = stderr if stderr else stdout
        return source.strip()[-200:]
    source = stderr if stderr.strip() else stdout
    for line in source.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""
