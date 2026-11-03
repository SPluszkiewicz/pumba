"""Regression guard for the system prompt: asserts key conventions and tool
names are still mentioned, so the prompt can't be silently gutted later.
"""

from __future__ import annotations

import re

from prompts import DIRECT_SYSTEM_PROMPT, SYSTEM_PROMPT, build_user_message, get_system_prompt
from tools_schema import TOOL_ENDPOINT_MAP


def test_get_system_prompt_returns_the_constant() -> None:
    assert get_system_prompt() == SYSTEM_PROMPT


def test_hpp_path_loads_ownership_rule() -> None:
    text = get_system_prompt(["src/AgentClient.hpp"])
    assert "Applicable project rules" in text
    assert "ownership" in text.lower()
    assert "unique_ptr" in text


def test_python_path_loads_pep8_and_vcs() -> None:
    text = get_system_prompt(["services/agent/main.py"])
    assert "Rule: python.md" in text
    assert "PEP 8" in text
    assert "Rule: vcs.md" in text
    assert "Rule: platforms.md" in text
    assert "Rule: cpp.md" not in text


def test_qml_path_loads_qt_review() -> None:
    text = get_system_prompt(["Main.qml"])
    assert "Rule: qt.md" in text
    assert "deleteLater" in text
    assert "Rule: cpp.md" not in text


def test_cmake_path_does_not_load_raii_rule_body_alone() -> None:
    text = get_system_prompt(["CMakeLists.txt"])
    assert "target_link_libraries" in text
    assert "Rule: cmake.md" in text
    assert "Rule: cpp.md" not in text


def test_mentions_cmake_conventions() -> None:
    text = SYSTEM_PROMPT.lower()
    assert "cmake" in text
    assert "target_link_libraries" in text or "target-based" in text


def test_mentions_core_guidelines_concepts() -> None:
    text = SYSTEM_PROMPT.lower()
    for anchor in ["core guidelines", "raii", "undefined behavior"]:
        assert anchor in text, f"expected system prompt to mention {anchor!r}"


def test_mentions_verification_via_tools_before_asserting() -> None:
    text = SYSTEM_PROMPT.lower()
    assert "verify" in text
    assert re.search(r"\btest\b", text)
    assert "build" in text


def test_mentions_every_registered_tool_name() -> None:
    text = SYSTEM_PROMPT.lower()
    for tool_name in TOOL_ENDPOINT_MAP:
        assert tool_name in text, f"expected system prompt to mention tool {tool_name!r}"


def test_asks_for_markdown_fenced_blocks() -> None:
    assert "markdown" in SYSTEM_PROMPT.lower()
    assert "```cpp" in SYSTEM_PROMPT
    assert "```diff" in SYSTEM_PROMPT
    assert "read-only" in SYSTEM_PROMPT.lower()
    assert "fenced code blocks" in DIRECT_SYSTEM_PROMPT.lower()


def test_instructions_are_appended_without_replacing_conventions() -> None:
    from prompts import append_instructions

    text = append_instructions(SYSTEM_PROMPT, "Prefer the project naming scheme.")
    assert "core guidelines" in text.lower()
    assert "do not replace" in text.lower()
    assert "Prefer the project naming scheme." in text
    assert append_instructions(SYSTEM_PROMPT, "  ") == SYSTEM_PROMPT


def test_project_rules_are_included_without_retrieved_paths(tmp_path, monkeypatch) -> None:
    rules = tmp_path / ".llm" / "rules"
    rules.mkdir(parents=True)
    (rules / "style.md").write_text("Use the project naming scheme.\n", encoding="utf-8")
    (rules / "huge.md").write_text("x" * 9000, encoding="utf-8")
    monkeypatch.setenv("AGENT_PROJECT_ROOT", str(tmp_path))

    text = get_system_prompt()

    assert "Use the project naming scheme." in text
    assert "Rule: .llm/rules/style.md" in text
    assert "x" * 100 not in text
    assert "Rule: cpp.md" not in text


def test_mentions_citation_instructions() -> None:
    text = SYSTEM_PROMPT.lower()
    assert "cit" in text  # cite / citation / citing


def test_build_user_message_with_context_includes_context_and_query() -> None:
    message = build_user_message("What does main() do?", "[1] app/src/main.cpp:1-10\nint main() {}", True)
    assert "What does main() do?" in message
    assert "[1] app/src/main.cpp:1-10" in message


def test_build_user_message_without_context_says_so() -> None:
    message = build_user_message("What does main() do?", "", False)
    assert "What does main() do?" in message
    assert "no" in message.lower()
    assert "context" in message.lower()
