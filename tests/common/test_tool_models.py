"""Tests for services/common/tool_models.py - the single source of truth for
tool arguments, and the anti-drift contract between `agent` and `tools`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.common import tool_models as tm  # noqa: E402


class TestSchemaGeneration:
    def test_one_function_per_spec_in_order(self) -> None:
        schema = tm.openai_tools_schema()
        assert [t["function"]["name"] for t in schema] == [s.name for s in tm.TOOL_SPECS]
        assert all(t["type"] == "function" for t in schema)

    def test_parameters_are_flat_object_schemas_without_titles(self) -> None:
        for tool in tm.openai_tools_schema():
            params = tool["function"]["parameters"]
            assert params["type"] == "object"
            assert "title" not in params
            assert isinstance(params["required"], list)
            for prop in params["properties"].values():
                assert "title" not in prop
                assert "anyOf" not in prop, "Optional[...] must be collapsed to a plain type"
                assert "description" in prop, "every field must carry an LLM-facing description"

    def test_required_matches_models(self) -> None:
        by_name = {t["function"]["name"]: t["function"]["parameters"] for t in tm.openai_tools_schema()}
        assert by_name["search"]["required"] == ["pattern"]
        assert by_name["read_file"]["required"] == ["path"]
        assert by_name["list_files"]["required"] == []
        assert by_name["lint_clang_tidy"]["required"] == ["paths"]
        assert by_name["build"]["required"] == []  # preset defaults server-side

    def test_bounds_are_in_the_schema(self) -> None:
        by_name = {t["function"]["name"]: t["function"]["parameters"]["properties"] for t in tm.openai_tools_schema()}
        assert by_name["search"]["max_results"]["maximum"] == tm.SEARCH_HARD_MAX_RESULTS
        assert by_name["search"]["max_results"]["minimum"] == 1
        assert by_name["list_files"]["max_results"]["maximum"] == tm.LIST_FILES_HARD_MAX_RESULTS
        assert by_name["read_file"]["start_line"] == {
            "type": "integer",
            "minimum": 1,
            "description": "Optional 1-based first line to return.",
        }

    def test_endpoint_map(self) -> None:
        mapping = tm.endpoint_map()
        assert mapping["read_file"] == {"method": "GET", "path": "/read-file"}
        assert mapping["search"] == {"method": "POST", "path": "/search"}
        assert set(mapping) == set(tm.TOOL_SPECS_BY_NAME)


class TestModels:
    def test_extra_keys_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            tm.SearchArgs.model_validate({"pattern": "x", "recursive": True})

    def test_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            tm.SearchArgs.model_validate({"pattern": "x", "max_results": tm.SEARCH_HARD_MAX_RESULTS + 1})
        with pytest.raises(ValidationError):
            tm.ReadFileArgs.model_validate({"path": "a", "start_line": 0})

    def test_preset_default_reads_env_at_call_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOOLS_CMAKE_PRESET", "debug")
        assert tm.BuildArgs().preset == "debug"
        monkeypatch.delenv("TOOLS_CMAKE_PRESET")
        assert tm.BuildArgs().preset == "release"

    def test_exclude_unset_forwards_only_what_the_model_said(self) -> None:
        assert tm.BuildArgs.model_validate({}).model_dump(exclude_unset=True) == {}
        assert tm.SearchArgs.model_validate({"pattern": "p"}).model_dump(exclude_unset=True) == {"pattern": "p"}


class TestContractWithToolsService:
    """Every ToolSpec must correspond to a real route on the `tools` FastAPI
    app, with the same method, and every argument field must be accepted by
    that route (body field for POST, query parameter for GET)."""

    @pytest.fixture(scope="class")
    def openapi(self) -> dict:
        from services.patch.main import app as patch_app
        from services.tools import main as tools_main

        paths = dict(tools_main.app.openapi()["paths"])
        paths.update(patch_app.openapi()["paths"])
        return {"paths": paths}

    def test_every_spec_has_a_route_with_same_method(self, openapi: dict) -> None:
        for spec in tm.TOOL_SPECS:
            assert spec.path in openapi["paths"], spec.name
            assert spec.method.lower() in openapi["paths"][spec.path], spec.name

    def test_get_specs_expose_every_field_as_query_param(self, openapi: dict) -> None:
        for spec in tm.TOOL_SPECS:
            if spec.method != "GET":
                continue
            op = openapi["paths"][spec.path]["get"]
            query_names = {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
            assert query_names == set(spec.model.model_fields), spec.name

    def test_post_specs_use_the_shared_model_as_body(self, openapi: dict) -> None:
        for spec in tm.TOOL_SPECS:
            if spec.method != "POST":
                continue
            op = openapi["paths"][spec.path]["post"]
            ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            assert ref.rsplit("/", 1)[-1] == spec.model.__name__, spec.name
