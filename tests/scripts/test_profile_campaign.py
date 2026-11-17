"""The RTX 6000 campaign planner. No GPU, no HTTP, no .env rewrite."""

from __future__ import annotations

from pathlib import Path

import profile_campaign as campaign


def test_profiles_follow_the_card_size():
    assert campaign.profiles_for_vram(16376) == []
    assert campaign.profiles_for_vram(24576) == ["24g", "24g-30b"]
    assert campaign.profiles_for_vram(46068) == ["48g", "48g-30b", "48g-32b"]
    assert campaign.profiles_for_vram(49152) == ["48g", "48g-30b", "48g-32b"]


def test_spill_when_gpu_memory_is_far_below_the_file():
    twenty_gib = 20 * 1024 * 1024 * 1024
    assert campaign.looks_spilled(4_000, twenty_gib)
    assert not campaign.looks_spilled(18_000, twenty_gib)


def test_loaded_ids_accept_router_and_openai_payloads():
    router = {"models": [{"id": "Qwen2.5-Coder-14B-Instruct-Q5_K_M", "status": {"value": "loaded"}}]}
    openai = {"data": [{"id": "Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf"}]}
    assert campaign.loaded_model_ids(router) == ["Qwen2.5-Coder-14B-Instruct-Q5_K_M"]
    assert campaign.loaded_model_ids(openai) == ["Qwen2.5-Coder-14B-Instruct-Q5_K_M"]


def test_s1_requires_the_known_bug_test_tool():
    calls = [{"name": "test", "arguments": {"filter": "known_bug"}, "result_summary": "failed"}]
    assert campaign.score_scenario("S1", "les tests échouent", calls) == "ok"
    assert campaign.score_scenario("S1", "known_bug échoue", []) == "raté"


def test_s2_needs_both_files_and_s3_rejects_an_invented_path():
    cited = "Le test tests/calculator_test.cpp attend 14. src/calculator.cpp ne le calcule pas."
    assert campaign.score_scenario("S2", cited, []) == "ok"
    assert campaign.score_scenario("S2", "ça vaut 14", []) == "raté"
    assert campaign.score_scenario("S3", "Le dépôt ne le contient pas.", []) == "ok"
    assert campaign.score_scenario("S3", "voir src/zorglub.cpp", []) == "raté"


def test_s4_and_s5_use_the_diff_and_the_apply_check():
    diff = """```diff
--- a/src/calculator.cpp
+++ b/src/calculator.cpp
@@ -1 +1 @@
-int left
+int term
```
"""
    both = """```diff
--- a/src/calculator.hpp
+++ b/src/calculator.hpp
@@ -1 +1 @@
+int Version();
--- a/src/calculator.cpp
+++ b/src/calculator.cpp
@@ -1 +1 @@
+int Version() { return 1; }
```
"""
    assert campaign.score_scenario("S4", diff, [], diff_applies=True) == "ok"
    assert campaign.score_scenario("S4", diff, [], diff_applies=False) == "raté"
    assert campaign.score_scenario("S5", both, [], diff_applies=True) == "ok"


def test_recreate_passes_bare_compose_argv(monkeypatch):
    calls: list[list[str]] = []

    class _Deploy:
        def _require_podman(self):
            return None

        def _compose_cmd(self, *_parts):
            raise AssertionError("_run already applies overlays")

        def _run(self, cmd, **_k):
            calls.append(cmd)

    monkeypatch.setattr(campaign, "_deploy", lambda: _Deploy())
    campaign._recreate(["llm"], build_embedder=True)
    assert calls[0] == ["podman", "compose", "build", "embedder"]
    assert calls[1][:6] == ["podman", "compose", "up", "-d", "--no-deps", "--force-recreate"]
    assert "llm" in calls[1]


def test_run_profile_calls_the_deploy_wrapper(monkeypatch, tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("EMBEDDER_GPU_ARG=0\n", encoding="utf-8")
    weight = tmp_path / "model.gguf"
    weight.write_bytes(b"gguf")
    called: list[list[str]] = []

    def fake_run(cmd, **_k):
        called.append(list(cmd))

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(campaign, "ENV_FILE", env)
    monkeypatch.setattr(campaign, "_profile_model_file", lambda _name: ("model", weight))
    monkeypatch.setattr(campaign.subprocess, "run", fake_run)
    monkeypatch.setattr(campaign, "_recreate", lambda *_a, **_k: None)
    monkeypatch.setattr(campaign, "_wait_healthy", lambda *_a, **_k: False)

    campaign._run_profile(
        "24g",
        workspace=str(tmp_path),
        agent_url="http://agent",
        llm_url="http://llm",
        bearer="token",
        load_timeout=1,
        chat_timeout=1,
    )
    assert called, "gpu-profile must run"
    assert Path(called[0][1]) == campaign.REPO_ROOT / "scripts" / "deploy.py"
    assert called[0][2:] == ["gpu-profile", "24g"]


def test_plan_does_not_call_the_stack(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("network or deploy should not run")

    monkeypatch.setattr(campaign, "query_nvidia_smi", lambda: (1000, 24576))
    monkeypatch.setattr(campaign, "_run_profile", fail)
    assert campaign.main(["--vram", "24576"]) == 0


def test_evo_plan_names_the_host_and_the_prompt_file(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise AssertionError("network or deploy should not run")

    monkeypatch.setattr(campaign, "query_nvidia_smi", lambda: None)
    monkeypatch.setattr(campaign, "_run_profile", fail)
    assert campaign.main(["--profiles", "evo-x5"]) == 0
    out = capsys.readouterr().out
    assert "evo-x5" in out
    assert "evo_campaign_prompts.json" in out
    assert "conteneur llm" in out


def test_evo_profile_keeps_the_shard_directory():
    stem, weight = campaign._profile_model_file("evo-x5")
    assert campaign._profile_is_host("evo-x5")
    assert stem == "Mistral-Small-4-119B-2603-UD-Q5_K_M-00001-of-00003"
    assert weight.parts[-2:] == (
        "UD-Q5_K_M",
        "Mistral-Small-4-119B-2603-UD-Q5_K_M-00001-of-00003.gguf",
    )
    assert campaign._published_model_id("evo-x5", stem) == "mistral-small-4"
    cuda_stem, cuda_weight = campaign._profile_model_file("24g")
    assert cuda_weight.name == f"{cuda_stem}.gguf"
    assert cuda_weight.parent.name == "models"


def test_custom_prompt_id_is_timed_not_scored_as_sample():
    assert campaign.outcome("T1", "n'importe quelle réponse", []) == "mesuré"
    assert campaign.outcome("S3", "voir src/zorglub.cpp", []) == "raté"


def test_metric_deltas_become_the_same_rates_as_the_ping():
    text = """
# HELP llamacpp:prompt_tokens_total n
llamacpp:prompt_tokens_total 10
llamacpp:prompt_seconds_total 0.5
llamacpp:tokens_predicted_total{model="mistral-small-4"} 4
llamacpp:tokens_predicted_seconds_total 2
"""
    before = campaign.parse_llama_metrics(text)
    after = campaign.parse_llama_metrics(
        "\n".join(
            [
                "llamacpp:prompt_tokens_total 30",
                "llamacpp:prompt_seconds_total 1.5",
                "llamacpp:tokens_predicted_total 12",
                "llamacpp:tokens_predicted_seconds_total 4",
            ]
        )
    )
    timings = campaign.metric_timings(before, after)
    assert timings["prompt_tokens"] == 20
    assert timings["prompt_ms"] == 1000
    assert timings["prompt_per_second"] == 20
    assert timings["predicted_n"] == 8
    assert timings["predicted_per_second"] == 4
    assert campaign.chat_timings({"timings": {"predicted_per_second": 3.5, "ignored": 1}}) == {
        "predicted_per_second": 3.5
    }


def test_load_prompts_rejects_a_duplicate_id(tmp_path: Path):
    path = tmp_path / "prompts.json"
    path.write_text('[{"id": "T1", "prompt": "a"}, {"id": "T1", "prompt": "b"}]', encoding="utf-8")
    try:
        campaign.load_prompts(path)
    except SystemExit as exc:
        assert "double" in str(exc)
    else:
        raise AssertionError("duplicate id must fail")


def test_host_run_measures_a_custom_prompt_without_the_llm_container(monkeypatch, tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("EMBEDDER_GPU_ARG=0\n", encoding="utf-8")
    weight = tmp_path / "shard.gguf"
    weight.write_bytes(b"gguf")
    recreated: list[list[str]] = []
    started: list[int] = []

    class _Deploy:
        HOST_LLM_READY_TIMEOUT = 900

        def _stop_llm_container(self):
            recreated.append(["stop-llm"])

        def _start_host_llm(self, timeout=300):
            started.append(timeout)

    monkeypatch.setattr(campaign, "ENV_FILE", env)
    monkeypatch.setattr(campaign, "_profile_is_host", lambda _name: True)
    monkeypatch.setattr(campaign, "_profile_model_file", lambda _name: ("stem", weight))
    monkeypatch.setattr(campaign, "_published_model_id", lambda _name, _stem: "mistral-small-4")
    monkeypatch.setattr(campaign, "_deploy", lambda: _Deploy())

    def fake_run(cmd, **_k):
        assert cmd[2:] == ["gpu-profile", "evo-x5"]

        class _Done:
            returncode = 0

        return _Done()

    monkeypatch.setattr(campaign.subprocess, "run", fake_run)
    monkeypatch.setattr(campaign, "_recreate", lambda services, **_k: recreated.append(list(services)))
    monkeypatch.setattr(campaign, "_wait_healthy", lambda *_a, **_k: True)
    monkeypatch.setattr(campaign, "query_nvidia_smi", lambda: (100, 100))

    def get_json(url, timeout):
        assert url.endswith("/v1/models")
        return {"data": [{"id": "mistral-small-4"}]}

    ticks = {"n": 0}

    def get_text(url, timeout):
        ticks["n"] += 1
        return (
            f"llamacpp:tokens_predicted_total {4 * ticks['n']}\n"
            f"llamacpp:tokens_predicted_seconds_total {ticks['n']}\n"
        )

    posts: list[tuple[str, dict]] = []

    def post_json(url, body, timeout, bearer):
        posts.append((url, body))
        if url.endswith("/chat"):
            return {"answer": "réponse libre", "tool_calls": []}
        return {"timings": {"prompt_ms": 12, "predicted_n": 2, "predicted_per_second": 3.5}}

    monkeypatch.setattr(campaign, "_get_json", get_json)
    monkeypatch.setattr(campaign, "_get_text", get_text)
    monkeypatch.setattr(campaign, "_post_json", post_json)

    row = campaign._run_profile(
        "evo-x5",
        workspace=str(tmp_path),
        agent_url="http://agent",
        llm_url="http://llm",
        bearer="token",
        load_timeout=300,
        chat_timeout=1,
        scenarios=({"id": "T1", "prompt": "question sur le grand dépôt"},),
    )
    assert ["llm"] not in recreated
    assert ["agent"] in recreated
    assert ["stop-llm"] in recreated
    assert started == [900]
    assert row["status"] == "ok"
    assert row["model"] == "mistral-small-4"
    assert row["s0_timings"]["prompt_ms"] == 12
    assert row["tokens_per_second"] == 3.5
    assert row["scenarios"]["T1"]["status"] == "mesuré"
    assert row["scenarios"]["T1"]["answer"] == "réponse libre"
    assert "seconds" in row["scenarios"]["T1"]
    assert row["scenarios"]["T1"]["timings"]["predicted_per_second"] == 4
    assert posts[0][1]["model"] == "mistral-small-4"
    assert "spilled" not in row
