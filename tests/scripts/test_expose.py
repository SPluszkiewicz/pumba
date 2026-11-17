"""Allowlist and relay for `deploy.py expose`. No firewall changes."""

from __future__ import annotations

import socket
import threading

import pytest

from deploy.expose import (
    firewall_allow_command,
    load_allowlist,
    parse_allowlist,
    peer_allowed,
    serve,
)


def test_parse_allowlist_keeps_addresses_and_comments() -> None:
    assert parse_allowlist(["# desk", "", "192.168.1.40", "10.0.0.2  # lab", "192.168.1.40"]) == (
        "192.168.1.40",
        "10.0.0.2",
    )


@pytest.mark.parametrize("token", ["*", "0.0.0.0", "0.0.0.0/0", "10.0.0.0/24", "not-an-ip", "::1"])
def test_parse_allowlist_rejects_open_ended_entries(token: str) -> None:
    with pytest.raises(ValueError):
        parse_allowlist([token])


def test_empty_allowlist_is_empty() -> None:
    assert parse_allowlist(["", "# nothing"]) == ()


def test_peer_allowed_matches_the_normalized_address() -> None:
    allowed = ("192.168.1.40",)
    assert peer_allowed("192.168.1.40", allowed)
    assert not peer_allowed("192.168.1.41", allowed)
    assert not peer_allowed("not-an-ip", allowed)


def test_firewall_rule_names_only_the_listed_machines() -> None:
    command = firewall_allow_command(8090, ("192.168.1.40", "10.0.0.2"))
    script = command[-1]
    assert "RemoteAddress 192.168.1.40,10.0.0.2" in script
    assert "LocalPort 8090" in script
    assert "0.0.0.0" not in script
    with pytest.raises(ValueError):
        firewall_allow_command(8090, ())


def test_load_allowlist_reads_the_file_and_extra_addresses(tmp_path) -> None:
    path = tmp_path / "allow.txt"
    path.write_text("192.168.1.40\n", encoding="utf-8")
    assert load_allowlist(path, ("10.0.0.2",)) == ("192.168.1.40", "10.0.0.2")


def test_relay_forwards_an_allowed_peer() -> None:
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    upstream.settimeout(2)
    stop = threading.Event()
    listener = serve(stop, "127.0.0.1", 0, "127.0.0.1", upstream.getsockname()[1], ("127.0.0.1",))
    try:
        client = socket.create_connection(("127.0.0.1", listener.getsockname()[1]), timeout=2)
        client.sendall(b"ping")
        remote, _addr = upstream.accept()
        assert remote.recv(4) == b"ping"
        remote.close()
        client.close()
    finally:
        stop.set()
        listener.close()
        upstream.close()


def test_relay_closes_a_peer_that_is_not_listed() -> None:
    stop = threading.Event()
    listener = serve(stop, "127.0.0.1", 0, "127.0.0.1", 9, ("203.0.113.5",))
    try:
        client = socket.create_connection(("127.0.0.1", listener.getsockname()[1]), timeout=2)
        client.settimeout(2)
        assert client.recv(16) == b""
        client.close()
    finally:
        stop.set()
        listener.close()
