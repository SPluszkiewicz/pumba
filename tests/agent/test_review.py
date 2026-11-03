"""Review comments are kept only when the snippet sits on a real diff line."""

from __future__ import annotations

import json

import httpx
import pytest
import review

DIFF = """\
diff --git a/src/a.cpp b/src/a.cpp
--- a/src/a.cpp
+++ b/src/a.cpp
@@ -10,3 +20,4 @@
 int keep() {
-    return 0;
+    return leaked;
 }
"""


def test_resolve_snippet_uses_the_new_file_line() -> None:
    assert review.resolve_snippet(DIFF, "return leaked") == 21


def test_resolve_snippet_rejects_a_short_or_missing_snippet() -> None:
    assert review.resolve_snippet(DIFF, "return") is None
    assert review.resolve_snippet(DIFF, "this line was invented") is None


@pytest.mark.asyncio
async def test_review_drops_unanchored_comments(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/git/diff-names"):
            return httpx.Response(200, json={"ok": True, "stdout": "src/a.cpp\n", "stderr": "", "exit_code": 0})
        if request.url.path.endswith("/git/diff"):
            return httpx.Response(200, json={"ok": True, "stdout": DIFF, "stderr": "", "exit_code": 0})
        raise AssertionError(request.url.path)

    async def fake_completion(*_args, **_kwargs):
        body = {
            "comments": [
                {"snippet": "return leaked", "text": "the pointer is not freed"},
                {"snippet": "this is not in the diff at all", "text": "invented"},
            ]
        }
        return {"choices": [{"message": {"content": json.dumps(body)}}]}

    monkeypatch.setattr(review, "chat_completion", fake_completion)
    monkeypatch.setattr(review, "TOOLS_BASE_URL", "http://tools.test")
    monkeypatch.setattr(review, "rules_for", lambda paths: "")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await review.review_change(client, "HEAD~1", "HEAD", "req-1")

    assert result["ok"] is True
    assert result["comments"] == [
        {
            "path": "src/a.cpp",
            "line": 21,
            "snippet": "return leaked",
            "text": "the pointer is not freed",
        }
    ]
    assert result["dropped"] == 1
