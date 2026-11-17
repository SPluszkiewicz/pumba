"""Forward the loopback doors to a list of machines.

The containers stay on 127.0.0.1. This process listens on one LAN address
and copies a connection to that same port on 127.0.0.1 only when the
source address is on the list. Embedder, Qdrant, and the tool services
are not in the list.

    python scripts/deploy.py expose --allow 192.168.1.40
    python scripts/deploy.py expose
    python scripts/deploy.py expose --down

`expose` with no --allow reads state/expose-allow.txt (one IPv4 per line).
An empty list does not open the ports. Ctrl+C closes the forwarders and
removes the Windows firewall rules this command added.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable, Sequence

import repo

ALLOW_FILE = repo.ROOT / "state" / "expose-allow.txt"
DOORS: tuple[tuple[int, str, str], ...] = (
    (8080, "model", "/v1"),
    (8090, "page", "/ui"),
    (8092, "mcp", "/mcp"),
    (3000, "grafana", "/"),
)
RULE_PREFIX = "pumba-expose"


def parse_allowlist(lines: Iterable[str]) -> tuple[str, ...]:
    """One IPv4 per line. Blank lines and # comments are ignored.

    A network (10.0.0.0/8), a wildcard, or 0.0.0.0 is rejected: an empty
    or universal list must not become "anyone".
    """
    found: list[str] = []
    for raw in lines:
        token = raw.split("#", 1)[0].strip()
        if not token:
            continue
        if token in {"*", "0.0.0.0/0"} or "/" in token:
            raise ValueError(f"not a single address: {token}")
        try:
            addr = ipaddress.ip_address(token)
        except ValueError as exc:
            raise ValueError(f"not an IPv4 address: {token}") from exc
        if not isinstance(addr, ipaddress.IPv4Address):
            raise ValueError(f"IPv4 only: {token}")
        if addr.is_unspecified or addr.is_multicast or int(addr) == 0xFFFFFFFF:
            raise ValueError(f"not a machine address: {token}")
        text = str(addr)
        if text not in found:
            found.append(text)
    return tuple(found)


def peer_allowed(peer: str, allowed: Sequence[str]) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return str(addr) in allowed


def rule_name(port: int) -> str:
    return f"{RULE_PREFIX}-{port}"


def firewall_allow_command(port: int, allowed: Sequence[str]) -> list[str]:
    if not allowed:
        raise ValueError("refusing a firewall rule with no remote address")
    remote = ",".join(allowed)
    name = rule_name(port)
    script = (
        f"Remove-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue; "
        f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound "
        f"-Action Allow -Protocol TCP -LocalPort {port} -RemoteAddress {remote} -Profile Any"
    )
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def firewall_remove_command(port: int) -> list[str]:
    script = f"Remove-NetFirewallRule -DisplayName '{rule_name(port)}' -ErrorAction SilentlyContinue"
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


def load_allowlist(path: Path, extra: Sequence[str]) -> tuple[str, ...]:
    lines: list[str] = []
    if path.is_file():
        lines.extend(path.read_text(encoding="utf-8").splitlines())
    lines.extend(extra)
    return parse_allowlist(lines)


def default_bind_address() -> str:
    """IPv4 chosen by the default route. UDP connect does not send a packet."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        addr = sock.getsockname()[0]
    except OSError as exc:
        raise SystemExit(f"[deploy] FATAL: no LAN address ({exc})") from exc
    finally:
        sock.close()
    if not addr or addr.startswith("127."):
        raise SystemExit("[deploy] FATAL: the default route is loopback. Pass --bind.")
    return addr


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _bridge(client: socket.socket, target_host: str, target_port: int) -> None:
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=5)
    except OSError:
        client.close()
        return
    left = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
    right = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
    left.start()
    right.start()
    left.join()
    right.join()
    for sock in (client, upstream):
        try:
            sock.close()
        except OSError:
            pass


def serve(
    stop: threading.Event,
    bind: str,
    listen_port: int,
    target_host: str,
    target_port: int,
    allowed: Sequence[str],
) -> socket.socket:
    """Listen until `stop` is set. Caller closes the returned socket to unblock."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((bind, listen_port))
    listener.listen(16)
    listener.settimeout(0.5)

    def _accept() -> None:
        while not stop.is_set():
            try:
                client, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peer = addr[0]
            if not peer_allowed(peer, allowed):
                client.close()
                print(f"[deploy] expose: refused {peer} on {listen_port}", flush=True)
                continue
            threading.Thread(
                target=_bridge,
                args=(client, target_host, target_port),
                daemon=True,
            ).start()

    threading.Thread(target=_accept, name=f"expose-{listen_port}", daemon=True).start()
    return listener


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def apply_firewall(ports: Sequence[int], allowed: Sequence[str], runner: Runner = _run) -> None:
    if sys.platform != "win32":
        print("[deploy] expose: no Windows firewall on this OS. The relay still drops other addresses.", flush=True)
        return
    for port in ports:
        result = runner(firewall_allow_command(port, allowed))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            print(
                f"[deploy] expose: firewall rule for {port} was not added ({detail}). "
                "Re-run this command in an elevated PowerShell if Windows drops the packets.",
                flush=True,
            )


def remove_firewall(runner: Runner = _run) -> None:
    if sys.platform != "win32":
        return
    for port, _name, _path in DOORS:
        runner(firewall_remove_command(port))


def cmd_expose(args: argparse.Namespace) -> int:
    if args.down:
        remove_firewall()
        print("[deploy] expose: firewall rules removed.", flush=True)
        return 0

    try:
        allowed = load_allowlist(ALLOW_FILE, tuple(args.allow or ()))
    except ValueError as exc:
        sys.exit(f"[deploy] FATAL: {exc}")
    if not allowed:
        sys.exit(
            "[deploy] FATAL: no machine listed. Pass --allow 192.168.1.40 "
            f"or write one IPv4 per line in {ALLOW_FILE}. "
            "An empty list does not open the ports."
        )

    bind = args.bind or default_bind_address()
    try:
        ipaddress.IPv4Address(bind)
    except ValueError:
        sys.exit(f"[deploy] FATAL: --bind must be an IPv4 address, not {bind!r}")

    stop = threading.Event()
    listeners: list[socket.socket] = []
    opened: list[int] = []
    firewall_ports: list[int] = []
    for port, name, path in DOORS:
        try:
            listeners.append(serve(stop, bind, port, "127.0.0.1", port, allowed))
        except OSError as exc:
            print(f"[deploy] expose: {name} port {port} not forwarded ({exc}).", flush=True)
            firewall_ports.append(port)
            continue
        opened.append(port)
        firewall_ports.append(port)
        suffix = "" if path == "/" else path
        print(f"[deploy] expose: {name} http://{bind}:{port}{suffix}", flush=True)

    if not opened and not firewall_ports:
        sys.exit("[deploy] FATAL: nothing to publish.")

    print(f"[deploy] expose: allowed {', '.join(allowed)}", flush=True)
    print("[deploy] expose: Ctrl+C closes the forwarders.", flush=True)
    apply_firewall(firewall_ports, allowed)
    try:
        while True:
            stop.wait(3600)
    except KeyboardInterrupt:
        print("\n[deploy] expose: stopping.", flush=True)
    finally:
        stop.set()
        for listener in listeners:
            try:
                listener.close()
            except OSError:
                pass
        remove_firewall()
    return 0
