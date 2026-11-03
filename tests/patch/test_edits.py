"""Working-copy edits: exact match, allow list, budgets, no write until apply."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services.patch import edits


@pytest.fixture(autouse=True)
def _clear() -> None:
    edits.reset_store()


def _tree(tmp_path: Path) -> Path:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.cpp").write_text("int value = 1;\n", encoding="utf-8")
    (source / "b.cpp").write_text("int other = 2;\n", encoding="utf-8")
    (tmp_path / "plan.md").write_text("Change src/a.cpp and src/b.cpp.\n", encoding="utf-8")
    return tmp_path


def test_propose_does_not_write_and_apply_does(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    opened = edits.begin_plan(root, "plan.md")
    proposed = edits.propose_edit(root, opened["campaign_id"], "src/a.cpp", "int value = 1;", "int value = 3;")
    assert proposed["written"] is False
    assert "int value = 3;" in proposed["diff"]
    assert (root / "src" / "a.cpp").read_text(encoding="utf-8") == "int value = 1;\n"
    applied = edits.apply_proposal(root, opened["campaign_id"], proposed["proposal_id"])
    assert applied["written"] is True
    assert (root / "src" / "a.cpp").read_text(encoding="utf-8") == "int value = 3;\n"


def test_excerpt_must_match_once(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "src" / "a.cpp").write_text("int value = 1;\nint value = 1;\n", encoding="utf-8")
    opened = edits.begin_plan(root, "plan.md")
    with pytest.raises(edits.EditError, match="occurs 2 times"):
        edits.propose_edit(root, opened["campaign_id"], "src/a.cpp", "int value = 1;", "int value = 3;")


def test_path_outside_the_plan_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    opened = edits.begin_plan(root, "plan.md")
    (root / "src" / "c.cpp").write_text("int extra = 0;\n", encoding="utf-8")
    with pytest.raises(edits.EditError, match="not in the plan"):
        edits.propose_edit(root, opened["campaign_id"], "src/c.cpp", "int extra = 0;", "int extra = 4;")


def test_escape_and_dot_git_are_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    opened = edits.begin_plan(root, "plan.md", focus="src/a.cpp")
    with pytest.raises(edits.EditError):
        edits.propose_edit(root, opened["campaign_id"], "../outside.cpp", "x", "y")
    with pytest.raises(edits.EditError, match="not editable"):
        edits.propose_edit(root, opened["campaign_id"], ".git/config", "x", "y")


def test_second_apply_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    opened = edits.begin_plan(root, "plan.md")
    proposed = edits.propose_edit(root, opened["campaign_id"], "src/a.cpp", "int value = 1;", "int value = 3;")
    edits.apply_proposal(root, opened["campaign_id"], proposed["proposal_id"])
    with pytest.raises(edits.EditError, match="already applied"):
        edits.apply_proposal(root, opened["campaign_id"], proposed["proposal_id"])


def test_file_budget_and_per_file_budget(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "plan.md").write_text("Only src/a.cpp.\n", encoding="utf-8")
    opened = edits.begin_plan(root, "plan.md", max_files=1)
    campaign = edits._CAMPAIGNS[opened["campaign_id"]]
    campaign.allow.add("src/b.cpp")
    edits.propose_edit(root, opened["campaign_id"], "src/a.cpp", "int value = 1;", "int value = 3;")
    with pytest.raises(edits.EditError, match="file budget"):
        edits.propose_edit(root, opened["campaign_id"], "src/b.cpp", "int other = 2;", "int other = 5;")


def test_eight_edits_per_file(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    opened = edits.begin_plan(root, "plan.md")
    text = "int value = 1;"
    for index in range(edits.EDITS_PER_FILE):
        nxt = f"int value = {index + 2};"
        proposed = edits.propose_edit(root, opened["campaign_id"], "src/a.cpp", text, nxt)
        edits.apply_proposal(root, opened["campaign_id"], proposed["proposal_id"])
        text = nxt
    with pytest.raises(edits.EditError, match="edit budget"):
        edits.propose_edit(root, opened["campaign_id"], "src/a.cpp", text, "int value = 99;")


def test_hard_cap_is_500() -> None:
    with pytest.raises(edits.EditError, match="500"):
        edits.begin_plan(Path("."), "plan.md", max_files=501)


def test_git_apply_check_is_not_a_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path)
    opened = edits.begin_plan(root, "plan.md")
    seen: list[list[str]] = []

    def _run(argv, **_kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(edits.shutil, "which", lambda _name: "git")
    monkeypatch.setattr(edits.subprocess, "run", _run)
    edits.propose_edit(root, opened["campaign_id"], "src/a.cpp", "int value = 1;", "int value = 3;")
    assert seen == [["git", "apply", "--check", "-p1", "--unsafe-paths", "-"]]
    assert "commit" not in seen[0]


def test_solution_build_only_when_several_targets() -> None:
    assert edits.should_build_solution({"app"}) is False
    assert edits.should_build_solution({"app", "tests"}) is True


def test_focus_path_is_allowed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "plan.md").write_text("Only src/a.cpp.\n", encoding="utf-8")
    opened = edits.begin_plan(root, "plan.md", focus="src/b.cpp")
    proposed = edits.propose_edit(root, opened["campaign_id"], "src/b.cpp", "int other = 2;", "int other = 9;")
    assert proposed["path"] == "src/b.cpp"
