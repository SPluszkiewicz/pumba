"""Scorer for the EVO-X5 agent comparison. No GPU, no git, no worktree."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import agent_compare as campaign

ROOT = Path(__file__).resolve().parents[2]
BANNED = ("mcp", "lookup", "outline", "serena", "poteto", "pocock", "checklist", "copilot", "github")


def test_matrix_matches_the_three_layers():
    assert [cell.id for cell in campaign.cells_in("A")] == [
        "local-sans-mcp",
        "local-mcp",
        "local-mcp-high",
        "api-sans-mcp",
        "api-mcp",
        "copilot-sans-mcp",
        "copilot-mcp",
    ]
    assert len(campaign.cells_in("B")) == 6
    assert len(campaign.cells_in("C")) == 5
    high = [cell for cell in campaign.CELL_ORDER if cell.reasoning == "high"]
    assert [cell.id for cell in high] == ["local-mcp-high"]
    assert high[0].mcp is True and high[0].route == "local" and high[0].skill == "none"
    layer_b = campaign.cells_in("B")
    assert {cell.route for cell in layer_b} == {"local"}
    assert {cell.mcp for cell in layer_b} == {True}
    assert {cell.reasoning for cell in layer_b} == {"none"}
    assert {cell.id for cell in campaign.cells_in("C")} == {
        "local-mcp",
        "checklist",
        "poteto",
        "copilot-poteto",
        "copilot-pocock",
    }
    assert len(campaign.CELL_ORDER) * len(campaign.TASK_ORDER) * campaign.MIN_RUNS == 294
    assert "294" in campaign.plan_text()
    assert campaign.checklist_lines() == 15


def test_prompts_do_not_name_a_tool():
    for task in campaign.TASK_ORDER:
        text = campaign.prompt_text(task.id).casefold()
        for word in BANNED:
            assert word not in text
        assert "agents.md" not in text


def test_external_phrase_is_not_elsewhere_in_the_tree():
    ticket = ROOT / "campaign" / "external" / "ticket-441.md"
    if not ticket.is_file():
        return
    phrase = [line.strip() for line in ticket.read_text(encoding="utf-8").splitlines() if line.strip()][-1]
    assert phrase
    skip = {".git", ".venv", "models", "state", "site", "build", "__pycache__", ".mypy_cache", ".ruff_cache"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in skip]
        for name in filenames:
            path = Path(dirpath) / name
            if path.name == "ticket-441.md" or path.suffix not in {".py", ".md", ".diff", ".yml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert phrase.casefold() not in text.casefold(), path


def test_overlay_refuses_the_repo_and_hides_the_ticket(tmp_path: Path):
    try:
        campaign.apply_overlay(ROOT, "bug")
    except RuntimeError as exc:
        assert "dépôt" in str(exc)
    else:
        raise AssertionError("the campaign repo must not be patched")

    fixture = tmp_path / "campaign" / "fixture"
    fixture.mkdir(parents=True)
    shutil.copy(ROOT / "campaign" / "fixture" / "ledger.py", fixture / "ledger.py")
    external = tmp_path / "campaign" / "external"
    external.mkdir()
    (external / "ticket-441.md").write_text("secret\n", encoding="utf-8")

    campaign.apply_overlay(tmp_path, "fait-externe")
    assert not external.exists()

    shutil.copy(ROOT / "campaign" / "fixture" / "ledger.py", fixture / "ledger.py")
    campaign.apply_overlay(tmp_path, "bug")
    assert "total += line + 1" in (fixture / "ledger.py").read_text(encoding="utf-8")


def test_question_call_fails_precision_and_external_needs_the_phrase():
    question = campaign.blank_run("local-mcp", "question", 1, "abc")
    question["answer"] = "close_day retourne 0"
    question["mcp_calls"] = [{"name": "lookup"}]
    question["questions"] = 0
    question["question_minutes"] = 0
    question["minutes"] = 2
    question["task_tokens"] = 30
    scored = campaign.measure(question)
    assert scored["valid"] is True
    assert scored["auto_ok"] == 1
    assert scored["mcp_precision"] == 0.0
    assert scored["mcp_rappel"] is None

    external = campaign.blank_run("local-sans-mcp", "fait-externe", 1, "abc")
    external["answer"] = "je ne le vois pas"
    external["mcp_calls"] = []
    missed = campaign.measure(external, fact="jeton-externe")
    assert missed["auto_ok"] == 0
    assert missed["mcp_rappel"] == 0.0

    external["mcp_calls"] = [{"name": "lookup"}]
    external["answer"] = "Le plafond est jeton-externe."
    hit = campaign.measure(external, fact="jeton-externe")
    assert hit["auto_ok"] == 1
    assert hit["mcp_rappel"] == 1.0


def test_bug_proof_and_ledgers(tmp_path: Path):
    ledger = tmp_path / "ledger.py"
    shutil.copy(ROOT / "campaign" / "fixture" / "ledger.py", ledger)
    bug = campaign.blank_run("local-mcp", "bug", 1, "abc")
    bug["proof"] = [
        {"command": "python -m pytest campaign/fixture/test_ledger.py::test_two_lines_sum -q", "exit": 1},
        {"command": "python -m pytest campaign/fixture/test_ledger.py::test_two_lines_sum -q", "exit": 0},
    ]
    scored = campaign.measure(bug, ledger)
    assert scored["valid"] is True
    assert scored["auto_ok"] == 1
    assert scored["preuve"] == 1.0

    bug["proof"] = [
        {"command": "pytest a", "exit": 1},
        {"command": "pytest b", "exit": 0},
    ]
    assert campaign.measure(bug, ledger)["preuve"] == 0.0

    broken = tmp_path / "broken.py"
    broken.write_text("def close_day(\n", encoding="utf-8")
    assert campaign.measure(bug, broken)["auto_ok"] == 0
    assert campaign.measure(bug, broken)["valid"] is True

    few = campaign.blank_run("local-mcp", "quelques-lignes", 1, "abc")
    changed = tmp_path / "version.py"
    changed.write_text(
        (ROOT / "campaign" / "fixture" / "ledger.py").read_text(encoding="utf-8").replace("return 1", "return 2"),
        encoding="utf-8",
    )
    assert campaign.measure(few, changed)["auto_ok"] == 1

    refactor = campaign.blank_run("local-mcp", "refactor", 1, "abc")
    extracted = tmp_path / "extracted.py"
    extracted.write_text(
        "def version():\n"
        "    return 1\n"
        "def rounded(total):\n"
        "    if total % 2:\n"
        "        return total + 1\n"
        "    return total\n"
        "def close_day(lines):\n"
        "    if not lines:\n"
        "        return 0\n"
        "    total = 0\n"
        "    for line in lines:\n"
        "        total += line\n"
        "    return rounded(total)\n",
        encoding="utf-8",
    )
    assert campaign.measure(refactor, extracted)["auto_ok"] == 1
    assert campaign.measure(refactor, ledger)["auto_ok"] == 0


def test_harness_files_are_not_out_of_scope():
    diff = (
        "diff --git a/AGENTS.md b/AGENTS.md\n"
        "+++ b/AGENTS.md\n"
        "+hello\n"
        "diff --git a/README.md b/README.md\n"
        "+++ b/README.md\n"
        "+x\n"
        "-y\n"
        "diff --git a/campaign/fixture/ledger.py b/campaign/fixture/ledger.py\n"
        "+++ b/campaign/fixture/ledger.py\n"
        "+z\n"
        "diff --git a/campaign/external/ticket-441.md b/campaign/external/ticket-441.md\n"
        "--- a/campaign/external/ticket-441.md\n"
        "+++ /dev/null\n"
        "-secret\n"
    )
    assert campaign.out_of_scope_lines("bug", diff) == 2
    assert campaign.out_of_scope_lines("question", diff) == 3
    assert campaign.out_of_scope_lines("fait-externe", diff) == 3


def test_run_spread_wider_than_the_case_gap_is_not_a_choice():
    noisy = campaign.comparison([0, 1, 0], [0, 0, 1])
    assert noisy["status"] == "ne pas choisir"
    assert noisy["spread"] > noisy["gap"]
    clear = campaign.comparison([0, 0, 0], [1, 1, 1])
    assert clear["status"] == "droite"
    assert clear["spread"] == 0
    assert campaign.comparison([1, 1, 1], [1, 1])["status"] == "incomplet"


def _runs(cell: str, auto: int = 1, hors: int = 0, n: int = 3, commit: str = "abc") -> list[dict]:
    spec = campaign.CELLS[cell]
    rows = []
    for task in campaign.TASK_ORDER:
        for index in range(1, n + 1):
            rows.append(
                {
                    "cell": cell,
                    "task": task.id,
                    "run": index,
                    "commit": commit,
                    "model": "copilot-x" if spec.route == "copilot" else spec.id,
                    "reasoning_effort": "absent" if spec.reasoning == "noted" else spec.reasoning,
                    "temperature": None,
                    "subagent_models": {"code": "mistral-small-4"} if spec.skill == "poteto" else {},
                    "pocock_path": "scripted" if spec.skill == "pocock" else "",
                    "metrics": {
                        "valid": True,
                        "errors": [],
                        "auto_ok": auto,
                        "diff_hors_perimetre": hors,
                        "mcp_rappel": None,
                        "mcp_precision": None,
                        "preuve": None,
                        "questions": 0,
                        "question_minutes": 0,
                        "skill_tokens": 10,
                        "skill_tokens_reported": 80 if spec.skill == "poteto" else None,
                        "task_tokens": 10,
                        "minutes": 1,
                    },
                }
            )
    return rows


def _set(rows: list[dict], cell: str, task: str, **values: object) -> None:
    for row in rows:
        if row["cell"] == cell and row["task"] == task:
            row["metrics"].update(values)


def test_mcp_choice_requires_a_call_and_a_quiet_gap():
    runs = _runs("local-mcp") + _runs("local-sans-mcp", auto=0)
    _set(runs, "local-mcp", "fait-externe", auto_ok=1, mcp_rappel=1, mcp_precision=1)
    _set(runs, "local-sans-mcp", "fait-externe", auto_ok=0, mcp_rappel=0)
    effect = {(row["route"], row["task"], row["metric"]): row for row in campaign.mcp_effect(runs)}
    assert effect[("local", "fait-externe", "auto_ok")]["choice"] == "avec MCP"

    _set(runs, "local-mcp", "fait-externe", mcp_rappel=0)
    effect = {(row["route"], row["task"], row["metric"]): row for row in campaign.mcp_effect(runs)}
    assert effect[("local", "fait-externe", "auto_ok")]["choice"] == "succès sans appel"


def test_a_pack_earns_its_place_only_by_beating_the_checklist():
    tied = campaign.pack_report(_runs("agents") + _runs("checklist"))
    agents = next(row for row in tied if row["cell"] == "agents")
    assert agents["place"] == "pas de place"
    assert next(row for row in tied if row["cell"] == "local-mcp")["place"] == "référence"

    won = campaign.pack_report(_runs("agents") + _runs("checklist", auto=0))
    assert next(row for row in won if row["cell"] == "agents")["place"] == "place"

    narrower = campaign.pack_report(_runs("agents", hors=0) + _runs("checklist", hors=4))
    assert next(row for row in narrower if row["cell"] == "agents")["place"] == "place"

    lost = _runs("agents") + _runs("checklist", auto=0)
    _set(lost, "agents", "bug", auto_ok=0)
    _set(lost, "checklist", "bug", auto_ok=1)
    beaten = campaign.pack_report(lost)
    assert next(row for row in beaten if row["cell"] == "agents")["place"] == "pas de place"

    poteto = campaign.pack_report(_runs("poteto") + _runs("checklist", auto=0))
    poteto_row = next(row for row in poteto if row["cell"] == "poteto")
    skill = next(item for item in poteto_row["metrics"] if item["metric"] == "skill_tokens")
    assert skill["status"] == "ne pas choisir"
    assert "sources" in skill["reason"]


def test_interaction_keeps_the_checklist_verdict():
    runs = _runs("poteto", auto=0) + _runs("checklist") + _runs("copilot-poteto") + _runs("pocock", auto=0)
    runs += _runs("copilot-pocock")
    packs = campaign.pack_report(runs)
    interaction = campaign.interaction_report(runs, packs)
    places = {cell["id"]: cell["place"] for cell in interaction["cells"]}
    assert places["poteto"] == "pas de place"
    assert places["copilot-poteto"] == "pas de place"
    assert places["checklist"] == "barre"
    assert interaction["contrasts"][0]["note"].startswith("Cette interaction")


def test_mixed_commits_and_duplicate_runs_do_not_choose():
    mixed = campaign.decide(_runs("local-mcp", commit="aaa") + _runs("checklist", commit="bbb"))
    assert mixed["gate"]
    assert mixed["packs"] == []
    text = campaign.format_report(mixed)
    assert "Ne pas choisir" in text
    assert "Décision" in campaign.format_report(
        campaign.decide(_runs("local-mcp") + _runs("checklist"))
    )

    rows = _runs("local-mcp", n=1)
    rows.append(dict(rows[0]))
    assert campaign.duplicate_gate(rows)


def test_poteto_models_must_stay_frozen():
    runs = _runs("poteto") + _runs("checklist", auto=0)
    runs[0]["subagent_models"] = {"code": "autre"}
    report = campaign.pack_report(runs)
    assert next(row for row in report if row["cell"] == "poteto")["place"] == "ne pas choisir"


def test_4080_runs_the_same_cases_with_the_14b():
    run = campaign.blank_run("local-mcp", "question", 1, "abc", host="4080")
    assert run["model"] == "Qwen2.5-Coder-14B-Instruct-Q5_K_M"
    assert run["backend"] == "cuda"
    assert run["power_mode"] == "desktop"
    run["answer"] = "close_day retourne 0"
    run["temperature"] = 0.0
    run["questions"] = 0
    run["question_minutes"] = 0
    run["minutes"] = 1
    run["task_tokens"] = 4
    assert campaign.measure(run)["valid"] is True

    rehearsal = {
        "cell": "local-mcp",
        "task": "question",
        "run": 1,
        "commit": "abc",
        "host": "4080",
        "route": "local",
        "model": "Qwen2.5-Coder-14B-Instruct-Q5_K_M",
        "metrics": {"valid": True},
    }
    final = {
        "cell": "local-mcp",
        "task": "question",
        "run": 2,
        "commit": "abc",
        "host": "evo-x5",
        "route": "local",
        "model": "mistral-small-4",
        "metrics": {"valid": True},
    }
    gate = campaign.decide([rehearsal, final])["gate"]
    assert gate is not None
    assert "hôtes" in gate


def test_wrong_host_is_not_a_score():
    run = campaign.blank_run("local-mcp", "question", 1, "abc")
    run["host"] = "autre"
    run["answer"] = "retourne 0"
    assert campaign.measure(run)["valid"] is False


def test_plan_command_does_not_touch_git():
    assert campaign.main(["plan"]) == 0
