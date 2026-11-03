"""The RTX 6000 campaign planner. No GPU, no HTTP, no .env rewrite."""

from __future__ import annotations

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


def test_plan_does_not_call_the_stack(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("network or deploy should not run")

    monkeypatch.setattr(campaign, "query_nvidia_smi", lambda: (1000, 24576))
    monkeypatch.setattr(campaign, "_run_profile", fail)
    assert campaign.main(["--vram", "24576"]) == 0
