"""build_step reuses the configure cache and names one repair."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from services.tools.handlers import build as build_handler


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)


def test_cached_build_is_parallel_and_skips_configure(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "CMakeCache.txt").write_text("ready\n", encoding="utf-8")
    monkeypatch.setattr(build_handler, "persistent_build_dir", lambda *_a, **_k: cache)
    seen: list[list[str]] = []

    def _run(argv, **_kwargs):
        seen.append(list(argv))
        return _completed(argv, stdout="built\n")

    monkeypatch.setattr(build_handler.subprocess, "run", _run)
    result = build_handler.run_cached_build("release", tmp_path, 30, "calculator")
    assert result["ok"] is True
    assert result["repair"] == {"kind": "none"}
    assert seen == [["cmake", "--build", str(cache), "--parallel", "--target", "calculator"]]


def test_one_cpp_in_the_log_repairs_that_file(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "CMakeCache.txt").write_text("ready\n", encoding="utf-8")
    monkeypatch.setattr(build_handler, "persistent_build_dir", lambda *_a, **_k: cache)

    def _run(argv, **_kwargs):
        return _completed(argv, returncode=1, stderr="src/calculator.cpp:12: error: boom\n")

    monkeypatch.setattr(build_handler.subprocess, "run", _run)
    result = build_handler.run_cached_build("release", tmp_path, 30, "calculator")
    assert result["ok"] is False
    assert result["repair"] == {"kind": "compile_file", "path": "src/calculator.cpp"}


def test_several_cpp_files_rebuild_the_target() -> None:
    log = "src/a.cpp:1: error\nsrc/b.cpp:2: error\n"
    assert build_handler.repair_from_log(log) == {"kind": "build_step"}
