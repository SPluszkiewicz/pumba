"""Cancel kills the started process and the child it spawned."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from services.tools import cancel_group as tools_cancel


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_cancel_kills_the_process_tree(tmp_path: Path) -> None:
    module = tools_cancel
    original_popen = subprocess.Popen
    subprocess.Popen = module._GroupPopen  # type: ignore[misc]
    request_id = "cancel-" + module.__name__.replace(".", "-")
    token = module.bind_request_id(request_id)
    pid_file = tmp_path / "grandchild.txt"
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(sys.argv[1], 'w', encoding='utf-8').write(str(child.pid))\n"
            "time.sleep(60)\n",
            str(pid_file),
        ]
    )
    try:
        assert _wait_until(pid_file.exists, 5), "grandchild pid was not written"
        grandchild = int(pid_file.read_text(encoding="utf-8").strip())
        assert _alive(grandchild)
        module.kill_request(request_id)
        parent.wait(timeout=5)
        assert _wait_until(lambda: not _alive(grandchild), 5)
    finally:
        subprocess.Popen = original_popen
        module.reset_request_id(token)
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
