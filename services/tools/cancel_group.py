"""Kill the process group started for one ``X-Request-Id``.

``POST /cancel`` on ``tools`` answers 204 and, when a
child was started for that header, kills its group. With no child, the
answer is still 204.

Handlers keep calling ``subprocess.run``. Tool tests patch that name
(``services.tools.handlers.build.subprocess.run`` and the same attribute
on the other handlers). Replacing
those calls with ``Popen`` would make the patch miss. ``subprocess.run``
itself constructs ``subprocess.Popen``, so the wrapper sits on ``Popen``
and only changes a child when this request id is bound. Otherwise ``Popen``
is called with the arguments the caller passed.
"""

from __future__ import annotations

import contextvars
import os
import signal
import subprocess
import threading

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cancel_request_id", default=None
)
_lock = threading.Lock()
_groups: dict[str, set[int]] = {}
_cancelled: set[str] = set()
_inflight: dict[str, int] = {}
_installed = False
# Two services wrap Popen in one interpreter during tests. Each remembers
# its own id on the instance under a distinct name.
_REQUEST_ATTR = "_cancel_request_id_" + __name__.replace(".", "_")


class _GroupPopen(subprocess.Popen):
    """Session leader while a request id is bound, so ``killpg`` hits the children."""

    def __init__(self, *args, **kwargs) -> None:
        request_id = _request_id.get() or ""
        if request_id and os.name == "posix":
            # The pid is the process-group id only after setsid. killpg on a
            # child that stayed in our group would signal this service.
            kwargs = dict(kwargs)
            kwargs.pop("process_group", None)
            kwargs["start_new_session"] = True
        super().__init__(*args, **kwargs)
        setattr(self, _REQUEST_ATTR, request_id)
        if not request_id or self.pid is None:
            return
        with _lock:
            _groups.setdefault(request_id, set()).add(self.pid)
            must_kill = request_id in _cancelled
        if must_kill:
            _kill_group(self.pid)

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            request_id = getattr(self, _REQUEST_ATTR, "")
            _forget(request_id, getattr(self, "pid", None))


def _forget(request_id: str, pid: int | None) -> None:
    if not request_id or pid is None:
        return
    with _lock:
        group = _groups.get(request_id)
        if not group:
            return
        group.discard(pid)
        if not group:
            _groups.pop(request_id, None)


def _kill_group(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        if os.name == "posix":
            # Names are missing from the Windows stubs. The service image is Linux,
            # where this pid is a session leader and killpg takes its children too.
            killpg = getattr(os, "killpg")  # noqa: B009
            killpg(pid, getattr(signal, "SIGKILL"))  # noqa: B009
        else:
            _kill_windows_tree(pid)
    except OSError:
        return


def _kill_windows_tree(root: int) -> None:
    """Terminate ``root`` and its descendants. Windows has no process-group kill."""
    seen: set[int] = set()
    for _ in range(3):
        batch = [pid for pid in _windows_descendants(root) if pid not in seen]
        if not batch:
            break
        seen.update(batch)
        for pid in reversed(batch):
            _terminate_pid(pid)
    _terminate_pid(root)


def _terminate_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    kernel32, _entry_type, _invalid = _win32()
    handle = kernel32.OpenProcess(0x0001, False, pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess(handle, 1)
    finally:
        kernel32.CloseHandle(handle)


def _windows_descendants(root: int) -> list[int]:
    import ctypes

    kernel32, entry_type, invalid = _win32()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snapshot or snapshot == invalid:
        return []
    children: dict[int, list[int]] = {}
    try:
        entry = entry_type()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        while True:
            parent = int(entry.th32ParentProcessID)
            children.setdefault(parent, []).append(int(entry.th32ProcessID))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    found: list[int] = []
    stack = list(children.get(root, []))
    while stack:
        pid = stack.pop()
        if pid == root or pid in found:
            continue
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


_WIN32 = None


def _win32():  # noqa: ANN202 - WinDLL only exists on Windows
    """kernel32, the process-entry type, and INVALID_HANDLE_VALUE."""
    global _WIN32
    if _WIN32 is not None:
        return _WIN32
    import ctypes
    from ctypes import wintypes

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(_PROCESSENTRY32W)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, pointer]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, pointer]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    invalid = int(getattr(wintypes.HANDLE(-1), "value", -1) or -1)
    _WIN32 = (kernel32, _PROCESSENTRY32W, invalid)
    return _WIN32


def kill_request(request_id: str) -> None:
    """Kill every live child started for ``request_id``. Missing id is a no-op."""
    request_id = request_id.strip()
    if not request_id:
        return
    with _lock:
        _cancelled.add(request_id)
        pids = list(_groups.get(request_id, ()))
    for pid in pids:
        _kill_group(pid)
        _forget(request_id, pid)


def begin_http(request_id: str) -> None:
    """Count one in-flight HTTP call so cancel and the tool call share the id."""
    request_id = request_id.strip()
    if not request_id:
        return
    with _lock:
        _inflight[request_id] = _inflight.get(request_id, 0) + 1


def end_http(request_id: str) -> None:
    """Drop the cancel flag once every HTTP call with this id has returned.

    Live pids stay registered until the child exits or ``kill_request``
    runs, so a cancel that arrives as the tool call is still winding down
    still finds the group. The flag itself must not outlive the calls:
    a child started for a finished id has to be allowed to run.
    """
    request_id = request_id.strip()
    if not request_id:
        return
    with _lock:
        left = _inflight.get(request_id, 0) - 1
        if left <= 0:
            _inflight.pop(request_id, None)
            _cancelled.discard(request_id)
        else:
            _inflight[request_id] = left


def bind_request_id(request_id: str) -> contextvars.Token[str | None]:
    cleaned = request_id.strip()
    return _request_id.set(cleaned or None)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id.reset(token)


def install() -> None:
    """Point ``subprocess.Popen`` at the wrapper. ``subprocess.run`` stays put."""
    global _installed
    if _installed:
        return
    if subprocess.Popen is not _GroupPopen:
        subprocess.Popen = _GroupPopen  # type: ignore[misc]
    _installed = True


install()
