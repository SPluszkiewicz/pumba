"""Per-request checkout resolution."""

from __future__ import annotations

import json

import checkouts
import pytest
from checkouts import UnknownWorkspace, local_changes_note, resolve_workspace


def test_resolve_matches_windows_and_posix_spellings(tmp_path, monkeypatch) -> None:
    store = tmp_path / "workspaces.json"
    store.write_text(
        json.dumps(
            {
                "repos": {
                    "repo_abc": {
                        "vcs": "git",
                        "url": "https://git.example/proj",
                        "collection": "repo_abc",
                    }
                },
                "checkouts": [
                    {
                        "id": "co_1",
                        "path": "D:/svn/ticket-a",
                        "repo_id": "repo_abc",
                        "mount": "/checkouts/co_1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(checkouts, "REGISTRY_PATH", store)
    found = resolve_workspace(r"d:\svn\ticket-a")
    assert found.collection == "repo_abc"
    assert resolve_workspace("/mnt/d/svn/ticket-a").mount == "/checkouts/co_1"
    assert found.mount == "/checkouts/co_1"
    assert found.vcs == "git"


def test_unknown_checkout_is_refused(tmp_path, monkeypatch) -> None:
    store = tmp_path / "workspaces.json"
    store.write_text('{"repos": {}, "checkouts": []}\n', encoding="utf-8")
    monkeypatch.setattr(checkouts, "REGISTRY_PATH", store)
    with pytest.raises(UnknownWorkspace):
        resolve_workspace("D:/not/registered")


def test_local_note_keeps_dirty_files_out_of_the_index() -> None:
    note = local_changes_note("svn", "M  src/a.cpp\n")
    assert "not written into the shared index" in note
    assert "src/a.cpp" in note
    assert "another revision" in local_changes_note("git", "")
