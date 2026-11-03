"""Compilation database discovery. clangd is not started."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools.handlers.compdb import find_compile_commands, prepare_compile_commands


def test_finds_build_directory(tmp_path: Path) -> None:
    database = tmp_path / "build" / "compile_commands.json"
    database.parent.mkdir()
    database.write_text("[]", encoding="utf-8")
    assert find_compile_commands(tmp_path) == database


def test_rewrites_windows_paths_onto_the_mount(tmp_path: Path) -> None:
    source = tmp_path / "src" / "calculator.cpp"
    source.parent.mkdir()
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    database = tmp_path / "build" / "compile_commands.json"
    database.parent.mkdir()
    database.write_text(
        json.dumps(
            [
                {
                    "directory": "D:/svn/ticket/build",
                    "file": "D:/svn/ticket/src/calculator.cpp",
                    "command": "c++ -c src/calculator.cpp",
                }
            ]
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "scratch"
    prepared = prepare_compile_commands(database, tmp_path, dest)
    assert prepared == dest
    rewritten = json.loads((dest / "compile_commands.json").read_text(encoding="utf-8"))
    assert rewritten[0]["file"].endswith("src/calculator.cpp")
    assert Path(rewritten[0]["file"]).is_file()


def test_rewrites_checkout_includes_and_leaves_the_qt_kit(tmp_path: Path) -> None:
    source = tmp_path / "src" / "calculator.cpp"
    source.parent.mkdir()
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    database = tmp_path / "build" / "compile_commands.json"
    database.parent.mkdir()
    database.write_text(
        json.dumps(
            [
                {
                    "directory": "D:/svn/ticket/build",
                    "file": "D:/svn/ticket/src/calculator.cpp",
                    "command": "c++ -ID:/svn/ticket/src -IC:/Qt/6.5/include -c D:/svn/ticket/src/calculator.cpp",
                }
            ]
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "scratch"
    prepare_compile_commands(database, tmp_path, dest)
    command = json.loads((dest / "compile_commands.json").read_text(encoding="utf-8"))[0]["command"]
    assert (tmp_path / "src").as_posix() in command
    assert "C:/Qt/6.5/include" in command


def test_keeps_a_database_whose_files_already_exist(tmp_path: Path) -> None:
    source = tmp_path / "src" / "calculator.cpp"
    source.parent.mkdir()
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    database = tmp_path / "build" / "compile_commands.json"
    database.parent.mkdir()
    database.write_text(
        json.dumps([{"directory": str(tmp_path / "build"), "file": str(source), "command": "c++"}]),
        encoding="utf-8",
    )
    assert prepare_compile_commands(database, tmp_path, tmp_path / "scratch") == database.parent
