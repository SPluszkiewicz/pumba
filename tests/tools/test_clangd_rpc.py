"""clangd framing and location rendering. No real clangd binary."""

from __future__ import annotations

import io
import os
import threading

from services.tools.handlers.clangd_rpc import (
    ClangdSession,
    MessageReader,
    encode_message,
    format_document_symbols,
    format_locations,
    format_related_symbols,
)
from services.tools.handlers.lsp import run_definition


def test_reader_returns_each_framed_message() -> None:
    raw = encode_message({"jsonrpc": "2.0", "method": "window/logMessage", "params": {}})
    raw += encode_message({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})
    reader = MessageReader(io.BytesIO(raw))
    first = reader.read()
    second = reader.read()
    assert first is not None and first["method"] == "window/logMessage"
    assert second is not None and second["result"] == {"ok": True}


def test_session_ignores_notifications() -> None:
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    session = ClangdSession(os.fdopen(in_w, "wb", buffering=0), os.fdopen(out_r, "rb", buffering=0))
    incoming = os.fdopen(in_r, "rb", buffering=0)
    outgoing = os.fdopen(out_w, "wb", buffering=0)

    def respond() -> None:
        msg = MessageReader(incoming).read()
        assert msg is not None
        outgoing.write(encode_message({"jsonrpc": "2.0", "method": "window/logMessage", "params": {}}))
        outgoing.write(encode_message({"jsonrpc": "2.0", "id": msg["id"], "result": {"found": True}}))
        outgoing.flush()

    thread = threading.Thread(target=respond)
    thread.start()
    assert session.request("textDocument/definition", {}) == {"found": True}
    thread.join(timeout=5)


def test_buffered_pipe_does_not_block_waiting_for_4096_bytes() -> None:
    r_fd, w_fd = os.pipe()
    reader_stream = io.BufferedReader(os.fdopen(r_fd, "rb", buffering=0), buffer_size=4096)
    writer_stream = os.fdopen(w_fd, "wb", buffering=0)
    try:
        writer_stream.write(encode_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}))
        msg = MessageReader(reader_stream).read()
        assert msg is not None and msg["result"] == {"ok": True}
    finally:
        writer_stream.close()
        reader_stream.close()


def test_format_locations_is_one_based_and_stays_inside_the_tree() -> None:
    prefix = "file:///workspace/app/"
    text, truncated = format_locations(
        [
            {"uri": "file:///workspace/app/src/a.hpp", "range": {"start": {"line": 11, "character": 4}}},
            {"uri": "file:///etc/passwd", "range": {"start": {"line": 0, "character": 0}}},
        ],
        prefix,
        limit=1,
    )
    assert text.startswith("src/a.hpp:12:5")


def test_format_related_symbols_keeps_system_header_supertypes() -> None:
    prefix = "file:///workspace/app/"
    text, truncated = format_related_symbols(
        [
            {
                "name": "runtime_error",
                "uri": "file:///usr/include/c%2B%2B/13/stdexcept",
                "selectionRange": {"start": {"line": 218, "character": 8}},
            }
        ],
        prefix,
        role="to",
    )
    assert text == "runtime_error stdexcept:219"
    assert truncated is False
    assert "passwd" not in text
    assert truncated is False


def test_format_document_symbols_flattens_the_tree() -> None:
    text, truncated = format_document_symbols(
        [
            {
                "name": "Calculator",
                "kind": 5,
                "range": {"start": {"line": 2}},
                "children": [{"name": "add", "kind": 6, "selectionRange": {"start": {"line": 4}}}],
            }
        ]
    )
    assert "Calculator class 3" in text
    assert "add method 5" in text
    assert truncated is False


def test_format_related_symbols_keeps_only_the_checkout() -> None:
    prefix = "file:///workspace/app/"
    text, _truncated = format_related_symbols(
        [
            {
                "from": {
                    "name": "main",
                    "uri": "file:///workspace/app/src/main.cpp",
                    "range": {"start": {"line": 9}},
                }
            },
            {"from": {"name": "other", "uri": "file:///tmp/x.cpp", "range": {"start": {"line": 0}}}},
        ],
        prefix,
        role="from",
    )
    assert "main src/main.cpp:10" in text
    assert "other" not in text


def test_run_definition_uses_the_session(tmp_path, monkeypatch) -> None:
    source = tmp_path / "src"
    source.mkdir()
    header = source / "a.hpp"
    header.write_text("struct Widget;\n", encoding="utf-8")

    class FakeSession:
        _proc = None

        def notify(self, method: str, params: object) -> None:
            assert method == "textDocument/didOpen"

        def request(self, method: str, params: dict) -> list:
            assert method == "textDocument/definition"
            assert params["position"] == {"line": 0, "character": 7}
            return [{"uri": header.resolve().as_uri(), "range": {"start": {"line": 0, "character": 7}}}]

    monkeypatch.setattr("services.tools.handlers.lsp._session", lambda *args, **kwargs: FakeSession())
    body = run_definition("src/a.hpp", 1, 8, "release", tmp_path, 30)
    assert body["ok"] is True
    assert "src/a.hpp:1:8" in body["stdout"]
