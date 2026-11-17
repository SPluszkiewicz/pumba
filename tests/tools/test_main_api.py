"""HTTP-level tests for `services.tools.main` (request models, query params,
error mapping). Handlers themselves are covered by their own test modules;
here we only check that the FastAPI layer wires arguments through and maps
exceptions to the documented status codes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.tools import main as tools_main  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    root = tmp_path / "app"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.cpp").write_text("".join(f"// line {i}\n" for i in range(1, 21)))
    (root / "CMakeLists.txt").write_text("project(demo)\n")
    monkeypatch.setattr(tools_main, "PROJECT_ROOT", root)
    return TestClient(tools_main.app)


class TestReadFile:
    def test_window_query_params_are_forwarded(self, client: TestClient) -> None:
        resp = client.get("/read-file", params={"path": "src/main.cpp", "start_line": 3, "end_line": 4})
        assert resp.status_code == 200
        body = resp.json()
        assert body["stdout"] == "// line 3\n// line 4\n"
        assert body["start_line"] == 3 and body["end_line"] == 4
        assert body["total_lines"] == 20

    def test_bad_window_is_400_with_contract_shape(self, client: TestClient) -> None:
        resp = client.get("/read-file", params={"path": "src/main.cpp", "start_line": 5, "end_line": 2})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False and body["exit_code"] == -1
        assert "end_line" in body["stderr"]

    def test_zero_start_line_rejected_by_validation(self, client: TestClient) -> None:
        resp = client.get("/read-file", params={"path": "src/main.cpp", "start_line": 0})
        assert resp.status_code == 422

    def test_missing_file_is_404(self, client: TestClient) -> None:
        resp = client.get("/read-file", params={"path": "src/nope.cpp"})
        assert resp.status_code == 404

    def test_traversal_is_400(self, client: TestClient) -> None:
        resp = client.get("/read-file", params={"path": "../etc/passwd"})
        assert resp.status_code == 400


class TestRetiredRoutes:
    @pytest.mark.parametrize(
        ("method", "path", "kwargs"),
        [
            ("post", "/search", {"json": {"pattern": "x"}}),
            ("get", "/list-files", {}),
            ("post", "/lsp/definition", {"json": {"path": "src/main.cpp", "line": 1, "character": 1}}),
            ("post", "/lsp/references", {"json": {"path": "src/main.cpp", "line": 1, "character": 1}}),
            ("post", "/lsp/hover", {"json": {"path": "src/main.cpp", "line": 1, "character": 1}}),
            ("post", "/lsp/symbols", {"json": {"query": "Widget"}}),
        ],
    )
    def test_route_is_absent(self, client: TestClient, method: str, path: str, kwargs: dict) -> None:
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Not Found"


class TestCompileAndGitHttp:
    def test_compile_rejects_non_source(self, client: TestClient) -> None:
        resp = client.post("/compile", json={"path": "CMakeLists.txt", "preset": "release"})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_git_log_without_repo_is_400(self, client: TestClient) -> None:
        resp = client.post("/git/log", json={"max_count": 5})
        assert resp.status_code == 400
        assert "git" in resp.json()["stderr"].lower()

    def test_git_diff_rejects_bad_ref(self, client: TestClient) -> None:
        resp = client.post("/git/diff", json={"ref_a": "HEAD;rm", "ref_b": "HEAD"})
        assert resp.status_code == 400

    def test_svn_status_without_working_copy_is_400(self, client: TestClient) -> None:
        resp = client.post("/svn/status", json={})
        assert resp.status_code == 400
        assert ".svn" in resp.json()["stderr"]

    def test_svn_diff_rejects_a_one_sided_range(self, client: TestClient) -> None:
        resp = client.post("/svn/diff", json={"rev_from": "1"})
        assert resp.status_code == 400
        assert "together" in resp.json()["stderr"]


class TestHealthAndMetrics:
    def test_health_lists_missing_binaries(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "tools"
        assert "missing" in body
        assert isinstance(body["missing"], list)

    def test_health_strict_is_503_when_a_binary_is_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tools_main, "HEALTH_STRICT", True)
        monkeypatch.setattr(tools_main, "REQUIRED_BINARIES", ("definitely-not-installed-binary",))
        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.json()["ok"] is False
        assert "definitely-not-installed-binary" in resp.json()["missing"]

    def test_metrics_is_prometheus_text(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "http_request" in resp.text or "http_requests" in resp.text
