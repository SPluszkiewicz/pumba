"""Registry: one collection per remote, one mount per checkout."""

from __future__ import annotations

import json

import workspaces as registry


def test_second_ticket_reuses_the_repository(tmp_path, monkeypatch) -> None:
    first_tree = tmp_path / "ticket-a"
    second_tree = tmp_path / "ticket-b"
    first_tree.mkdir()
    second_tree.mkdir()
    store = tmp_path / "workspaces.json"
    monkeypatch.setattr(registry, "detect_remote", lambda _path: ("svn", "https://svn.example/proj"))

    first = registry.register_checkout(first_tree, registry_path=store)
    registry.mark_indexed(first.repo_id, registry_path=store)
    second = registry.register_checkout(second_tree, registry_path=store)

    assert first.needs_index
    assert not second.needs_index
    assert first.collection == second.collection
    assert first.checkout_id != second.checkout_id
    data = json.loads(store.read_text(encoding="utf-8"))
    assert len(data["checkouts"]) == 2
    assert data["repos"][first.repo_id]["indexed"] is True


def test_drive_letter_and_wsl_mount_are_the_same_tree() -> None:
    assert registry.path_spellings("D:/svn/ticket") == ["D:/svn/ticket", "/mnt/d/svn/ticket"]
    assert registry.path_spellings("/mnt/d/svn/ticket") == ["/mnt/d/svn/ticket", "D:/svn/ticket"]
    assert registry.path_spellings("/srv/checkouts/trunk") == ["/srv/checkouts/trunk"]


def test_native_linux_keeps_either_home_layout() -> None:
    for home in ("/home/user/rest/trunk", "/home/org/user/rest/trunk"):
        assert registry.path_spellings(home + "/") == [home]
        assert (
            registry.engine_bind_path(
                home,
                runtime="podman",
                host_os="posix",
                in_wsl=False,
                is_dir=lambda path, expected=home: path == expected,
            )
            == home
        )


def test_native_linux_uses_the_path_that_exists() -> None:
    native = "/srv/checkouts/trunk"
    assert (
        registry.engine_bind_path(
            native, runtime="podman", host_os="posix", in_wsl=False, is_dir=lambda path: path == native
        )
        == native
    )
    assert (
        registry.engine_bind_path(
            "D:/svn/ticket",
            runtime="podman",
            host_os="posix",
            in_wsl=False,
            is_dir=lambda _path: False,
        )
        == "D:/svn/ticket"
    )
    assert (
        registry.engine_bind_path(
            "D:/svn/ticket",
            runtime="podman",
            host_os="posix",
            in_wsl=True,
            is_dir=lambda _path: False,
        )
        == "/mnt/d/svn/ticket"
    )


def test_docker_on_windows_keeps_the_drive_path() -> None:
    assert (
        registry.engine_bind_path("D:/svn/ticket", runtime="docker", host_os="nt")
        == "D:/svn/ticket"
    )
    assert (
        registry.engine_bind_path("D:/svn/ticket", runtime="podman", host_os="nt")
        == "/mnt/d/svn/ticket"
    )


def test_patch_mount_is_writable_and_the_others_stay_read_only() -> None:
    data = {
        "checkouts": [
            {"path": "D:/svn/ticket", "mount": "/checkouts/co_abc"},
        ]
    }
    text = registry.compose_mounts_text(data, host_path_for=lambda raw: raw)
    assert '"D:/svn/ticket:/checkouts/co_abc:ro"' in text
    patch = text.split("  patch:", 1)[1]
    assert "/checkouts/co_abc:ro" not in patch
    assert "/checkouts/co_abc\"" in patch


def test_mcp_file_gains_the_workspace_header(tmp_path) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    registry.install_vscode_mcp(folder, "D:/svn/ticket-a")
    body = json.loads((folder / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert body["servers"]["local-llm"]["headers"]["X-Workspace"] == "D:/svn/ticket-a"

    registry.install_vscode_mcp(folder, "D:/svn/ticket-a")
    again = json.loads((folder / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert again == body
