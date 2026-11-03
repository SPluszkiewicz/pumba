#!/usr/bin/env python3
"""Compare ways of running a coding agent. Same repo, same commit, same tasks.

Three runs minimum per case (one cell, one task). The gap inside a case is
max minus min. If that gap is larger than the gap between two cases, the
status is ``ne pas choisir``. A pack earns a place only by beating the
checklist on auto success, or by tying that and touching fewer out-of-scope
lines, with no task lost and no noisy task. Layer C does not give the place
back.

    python scripts/host/agent_compare.py plan
    python scripts/host/agent_compare.py prepare --cell local-mcp --task fait-externe --run 1 --commit HEAD
    python scripts/host/agent_compare.py score --run state/compare/logs/.../run.json --worktree <worktree>
    python scripts/host/agent_compare.py decide state/compare/logs

Read the report in order: MCP effect on the external-fact task, then the
pack effect, then the interaction. The reasoning split (none / high) is only
the local model with MCP, and it does not move layers B or C.
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import re
import shutil
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONDUCTION = REPO_ROOT / "campaign" / "conduction"
TICKET = REPO_ROOT / "campaign" / "external" / "ticket-441.md"
TREE_ROOT = REPO_ROOT.parent / "local_llm-compare" / "trees"
LOG_ROOT = REPO_ROOT / "state" / "compare" / "logs"
MIN_RUNS = 3

# Same cases on both machines. The local weights change with the host.
# A decide folder that mixes the two is refused.
HOSTS: dict[str, dict[str, Any]] = {
    "evo-x5": {
        "local_model": "mistral-small-4",
        "backend": "vulkan",
        "power": "performance",
        "temperature_pair": True,
    },
    "4080": {
        "local_model": "Qwen2.5-Coder-14B-Instruct-Q5_K_M",
        "backend": "cuda",
        "power": "desktop",
        "temperature_pair": False,
    },
}

# Paths the harness writes. They are the treatment, not the agent's diff.
HARNESS_PATHS = {"AGENTS.md", "CHECKLIST.md", "SPEC.md"}
# max − min. A wider run range than the case gap is not a result.
_ANSWER_ZERO = re.compile(r"(?i)(retourne|renvoie|vaut|returns|return)\s+0\b")

DECISIONS = {
    "auto_ok": "Livrer ou non le changement de cette case.",
    "diff_hors_perimetre": "Accepter ou refuser le diff, selon les lignes hors du fichier désigné.",
    "mcp_rappel": "Laisser l'accès externe allumé pour un fait hors de l'arbre.",
    "mcp_precision": "Couper l'accès externe sur une question dont le code suffit. Un appel y est un échec.",
    "preuve": "Croire un test vert sans la commande collée en échec puis en succès.",
    "questions": "Laisser cette conduite attendre une personne.",
    "question_minutes": "Compter le temps humain dans le coût de la case.",
    "skill_tokens": "Imputer le texte du pack, à part de la tâche.",
    "task_tokens": "Imputer la tâche, à part du pack.",
    "minutes": "Garder la case sur cet hôte, selon le temps total.",
}
LOWER_IS_BETTER = {
    "diff_hors_perimetre",
    "questions",
    "question_minutes",
    "skill_tokens",
    "task_tokens",
    "minutes",
}
PACK_SKILLS = ("agents", "plan", "poteto", "pocock")
MCP_PAIRS = (
    ("local", "local-mcp", "local-sans-mcp"),
    ("api", "api-mcp", "api-sans-mcp"),
    ("copilot", "copilot-mcp", "copilot-sans-mcp"),
)
INTERACTION_NOTE = "Cette interaction ne rend pas sa place à un pack écarté par la checklist."
REASONING_NOTE = "Ce dédoublement ne déplace pas B ni C, qui restent sur reasoning none."


@dataclass(frozen=True)
class Cell:
    id: str
    layers: tuple[str, ...]
    route: str
    mcp: bool
    skill: str
    reasoning: str


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    mcp: str
    needs_ledger: bool
    scope: str


CELL_ORDER: tuple[Cell, ...] = (
    Cell("local-sans-mcp", ("A",), "local", False, "none", "none"),
    Cell("local-mcp", ("A", "B", "C"), "local", True, "none", "none"),
    Cell("local-mcp-high", ("A",), "local", True, "none", "high"),
    Cell("api-sans-mcp", ("A",), "api", False, "none", "none"),
    Cell("api-mcp", ("A",), "api", True, "none", "none"),
    Cell("copilot-sans-mcp", ("A",), "copilot", False, "none", "noted"),
    Cell("copilot-mcp", ("A",), "copilot", True, "none", "noted"),
    Cell("agents", ("B",), "local", True, "agents", "none"),
    Cell("plan", ("B",), "local", True, "plan", "none"),
    Cell("poteto", ("B", "C"), "local", True, "poteto", "none"),
    Cell("pocock", ("B",), "local", True, "pocock", "none"),
    Cell("checklist", ("B", "C"), "local", True, "checklist", "none"),
    Cell("copilot-poteto", ("C",), "copilot", True, "poteto", "noted"),
    Cell("copilot-pocock", ("C",), "copilot", True, "pocock", "noted"),
)
CELLS = {cell.id: cell for cell in CELL_ORDER}

TASK_ORDER: tuple[Task, ...] = (
    Task(
        "fait-externe",
        "Le ticket 441 fixe le plafond du journal. Que dit-il, mot pour mot, sur ce plafond ? "
        "Ce ticket n'est pas dans cet arbre.",
        "required",
        False,
        "",
    ),
    Task(
        "bug",
        "Cette commande échoue sur cet arbre :\n\n"
        "python -m pytest campaign/fixture/test_ledger.py::test_two_lines_sum -q\n\n"
        "Fais-la passer. Ne modifie pas le test.",
        "ignore",
        True,
        "ledger",
    ),
    Task("flou", "Améliore le journal.", "ignore", True, ""),
    Task(
        "refactor",
        "Sors l'arrondi de close_day dans une fonction rounded. "
        "Les résultats de close_day restent ceux des tests déjà écrits. Ne modifie pas ces tests.",
        "ignore",
        True,
        "fixture-not-tests",
    ),
    Task(
        "revue",
        "On te soumet le diff suivant. Dis s'il peut être appliqué. "
        "Ne le prends pas si le journal n'obéit plus aux tests déjà écrits.",
        "ignore",
        True,
        "",
    ),
    Task(
        "question",
        "Que retourne close_day quand on lui passe une liste vide ?",
        "forbidden",
        False,
        "",
    ),
    Task(
        "quelques-lignes",
        "Fais retourner 2 par version(). Le reste du journal ne change pas.",
        "ignore",
        True,
        "ledger",
    ),
)
TASKS = {task.id: task for task in TASK_ORDER}


def cells_in(layer: str) -> list[Cell]:
    return [cell for cell in CELL_ORDER if layer in cell.layers]


def prompt_text(task_id: str) -> str:
    """Task text. The review diff is appended here and is not a tool name."""
    text = TASKS[task_id].prompt
    if task_id == "revue":
        diff = (CONDUCTION / "mauvais.diff").read_text(encoding="utf-8").rstrip("\n")
        return f"{text}\n\n{diff}\n"
    return text


def fact_phrase(path: Path | None = None) -> str:
    """Last non-empty line of the local ticket. Absent from the commit on purpose."""
    source = path or TICKET
    lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"ticket vide : {source}")
    return lines[-1]


def injected_files(cell: Cell, task_id: str) -> list[Path]:
    if cell.skill == "agents":
        return [CONDUCTION / "AGENTS.md"]
    if cell.skill == "checklist":
        return [CONDUCTION / "checklist.md"]
    if cell.skill == "pocock":
        return [CONDUCTION / "specs" / f"{task_id}.md"]
    return []


def skill_tokens_files(cell_id: str, task_id: str) -> int:
    """Characters/4 of the files this case copies into the worktree.

    Poteto's skill text is not in this repo. That case uses the client's
    ``skill_tokens`` figure. Comparing the two sources is ``ne pas choisir``.
    """
    total = 0
    for path in injected_files(CELLS[cell_id], task_id):
        if path.is_file():
            total += len(path.read_text(encoding="utf-8")) // 4
    return total


def checklist_lines() -> int:
    text = (CONDUCTION / "checklist.md").read_text(encoding="utf-8")
    return len([line for line in text.splitlines() if line.strip()])


def apply_overlay(dest: Path, task_id: str) -> None:
    """Mutate a worktree. Refuses the campaign repo itself."""
    if dest.resolve() == REPO_ROOT.resolve():
        raise RuntimeError("refuse d'altérer le dépôt de campagne")
    if task_id not in TASKS:
        raise KeyError(task_id)
    if task_id == "fait-externe":
        external = dest / "campaign" / "external"
        if external.exists():
            shutil.rmtree(external)
        return
    if task_id != "bug":
        return
    path = dest / "campaign" / "fixture" / "ledger.py"
    text = path.read_text(encoding="utf-8")
    old = "        total += line\n"
    new = "        total += line + 1\n"
    if text.count(old) != 1:
        raise RuntimeError("ligne du bug introuvable")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def install_conduction(dest: Path, cell_id: str, task_id: str) -> None:
    cell = CELLS[cell_id]
    files = injected_files(cell, task_id)
    if not files:
        return
    source = files[0]
    if cell.skill == "agents":
        target = dest / "AGENTS.md"
    elif cell.skill == "checklist":
        target = dest / "CHECKLIST.md"
    else:
        target = dest / "SPEC.md"
    shutil.copyfile(source, target)


def _model_for(cell: Cell) -> str:
    if cell.route == "local":
        return "mistral-small-4"
    if cell.route == "api":
        return "mistral-small-2603"
    return ""


def _temperature_for(cell: Cell) -> float | None:
    if cell.route == "copilot":
        return None
    if cell.reasoning == "high":
        return 0.7
    return 0.1


def blank_run(cell_id: str, task_id: str, run: int, commit: str, host: str = "evo-x5") -> dict[str, Any]:
    cell = CELLS[cell_id]
    profile = HOSTS[host]
    if cell.route == "local":
        model = profile["local_model"]
        backend = profile["backend"]
        temperature = _temperature_for(cell) if profile["temperature_pair"] else None
    else:
        model = _model_for(cell)
        backend = "remote"
        temperature = _temperature_for(cell)
    return {
        "cell": cell.id,
        "task": task_id,
        "run": run,
        "commit": commit,
        "host": host,
        "route": cell.route,
        "model": model,
        "mcp": cell.mcp,
        "skill": cell.skill,
        "reasoning_effort": "" if cell.reasoning == "noted" else cell.reasoning,
        "temperature": temperature,
        "power_mode": profile["power"],
        "backend": backend,
        "workspace_is_worktree": True,
        "answer": "",
        "diff": "",
        "mcp_calls": [],
        "proof": [],
        "questions": None,
        "question_minutes": None,
        "task_tokens": None,
        "skill_tokens": None,
        "minutes": None,
        "plan_then_execute": None,
        "subagent_models": {},
        "pocock_path": "",
    }


def operator_notes(cell_id: str, task_id: str, run: int, commit: str, tree: Path, host: str = "evo-x5") -> str:
    cell = CELLS[cell_id]
    profile = HOSTS[host]
    mcp = "allumé" if cell.mcp else "éteint"
    lines = [
        f"Case {cell_id} / {task_id} / run {run}",
        f"Commit {commit}. Hôte {host}. Alimentation {profile['power']}.",
        f"Workspace de l'éditeur : seulement {tree}",
        "Le serveur MCP, s'il est allumé, indexe le dépôt principal, pas ce worktree.",
        "Le ticket 441 n'est pas dans le commit. Il reste dans le dépôt principal.",
        f"MCP {mcp}. Route {cell.route}. Conduite {cell.skill}.",
        "Nouvelle conversation. Colle PROMPT.md tel quel. Ne nomme pas d'outil dans un ajout.",
        "Après le run : git add -N -A puis git diff HEAD. Colle ce diff dans run.json.",
        "AGENTS.md, CHECKLIST.md et SPEC.md sont la conduite : le score les ignore.",
        "Trois runs minimum. Un quatrième ne sert pas à réduire un écart.",
    ]
    if cell.route == "local" and host == "4080":
        lines.append("Modèle local Qwen2.5-Coder-14B-Instruct-Q5_K_M. Backend cuda. Profil 16g.")
        lines.append("Mêmes tests que sur l'EVO-X5. Ce dossier reste séparé au moment de decide.")
    elif cell.route == "local":
        effort = cell.reasoning
        lines.append(f"llama-server déjà lancé : python scripts/host/evo_x5_serve.py --reasoning {effort}")
        lines.append("Modèle mistral-small-4. Backend vulkan. Ne pas démarrer le service compose llm.")
    elif cell.route == "api":
        lines.append("API Mistral, modèle mistral-small-2603, reasoning_effort none, température 0.1.")
    else:
        lines.append("Note le nom du modèle Copilot affiché, reasoning_effort (none, high ou absent).")
        lines.append("Le même nom et le même effort sur tous les runs Copilot de la campagne.")
    if cell.skill == "plan":
        lines.append("Premier tour : plan seul, aucun fichier touché. Deuxième tour : exécuter ce plan.")
        lines.append("plan_then_execute passe à true seulement si cet ordre a eu lieu.")
    if cell.skill == "poteto":
        lines.append("poteto-mode. Note le modèle de chaque sous-agent. Le même tableau sur les trois runs.")
        lines.append("skill_tokens est le décompte client du skill. Le dépôt ne contient pas ce texte.")
    if cell.skill == "pocock":
        lines.append("Lis SPEC.md. Les réponses humaines sont reponses.md, ici, pas dans le worktree.")
        lines.append("Si la question n'y est pas : ne réponds pas. implement, puis tdd, sur SPEC.md.")
        lines.append("pocock_path : scripted ou implement-tdd. Un seul des deux dans la case.")
    lines.append("questions et question_minutes : ce que tu as demandé, et les minutes avant la réponse.")
    lines.append("task_tokens : la tâche. skill_tokens : le pack, à part. minutes : le mur.")
    return "\n".join(lines) + "\n"


def assemble(
    tree: Path,
    log_dir: Path,
    cell_id: str,
    task_id: str,
    run: int,
    commit: str,
    host: str = "evo-x5",
) -> None:
    apply_overlay(tree, task_id)
    install_conduction(tree, cell_id, task_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "PROMPT.md").write_text(prompt_text(task_id).rstrip("\n") + "\n", encoding="utf-8")
    (log_dir / "OPERATOR.md").write_text(
        operator_notes(cell_id, task_id, run, commit, tree, host),
        encoding="utf-8",
    )
    if CELLS[cell_id].skill == "pocock":
        shutil.copyfile(CONDUCTION / "reponses.md", log_dir / "reponses.md")
    payload = blank_run(cell_id, task_id, run, commit, host)
    (log_dir / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(cell_id: str, task_id: str, run: int, commit: str, host: str = "evo-x5") -> int:
    if cell_id not in CELLS or task_id not in TASKS:
        print("[compare] case inconnue", file=sys.stderr)
        return 1
    if host not in HOSTS:
        print(f"[compare] hôte inconnu : {host}", file=sys.stderr)
        return 1
    try:
        sha = subprocess.check_output(["git", "rev-parse", commit], cwd=REPO_ROOT, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"[compare] commit introuvable : {commit}", file=sys.stderr)
        return 1
    tree = TREE_ROOT / host / f"{cell_id}__{task_id}__{run}"
    log_dir = LOG_ROOT / host / f"{cell_id}__{task_id}__{run}"
    if tree.exists() or log_dir.exists():
        print(f"[compare] déjà présent : {tree} ou {log_dir}", file=sys.stderr)
        return 1
    TREE_ROOT.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["git", "worktree", "add", "--detach", str(tree), sha],
        cwd=REPO_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        return completed.returncode or 1
    assemble(tree, log_dir, cell_id, task_id, run, sha, host)
    print(log_dir / "PROMPT.md", flush=True)
    return 0


def _is_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _close(value: Any, expected: float) -> bool:
    try:
        return abs(float(value) - expected) < 1e-9
    except (TypeError, ValueError):
        return False


def _nonnegative(value: Any) -> bool:
    if value is None:
        return True
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


def case_errors(run: dict[str, Any]) -> list[str]:
    """Identity checks. A wrong host, model, or power mode is not a score."""
    cell_id = run.get("cell")
    if cell_id not in CELLS:
        return ["case inconnue"]
    cell = CELLS[cell_id]
    errors: list[str] = []
    if run.get("task") not in TASKS:
        errors.append("tâche inconnue")
    profile = HOSTS.get(str(run.get("host") or ""))
    if profile is None:
        errors.append("hôte")
    elif run.get("power_mode") != profile["power"]:
        errors.append("P-MODE")
    if run.get("route") != cell.route:
        errors.append("route")
    if run.get("mcp") is not cell.mcp:
        errors.append("mcp")
    if run.get("skill") != cell.skill:
        errors.append("conduite")
    if not run.get("commit"):
        errors.append("commit")
    if run.get("workspace_is_worktree") is not True:
        errors.append("workspace")
    if not _nonnegative(run.get("questions")) or not _nonnegative(run.get("question_minutes")):
        errors.append("questions")
    if not _nonnegative(run.get("minutes")) or not _nonnegative(run.get("task_tokens")):
        errors.append("tokens ou minutes")
    if not isinstance(run.get("mcp_calls"), list):
        errors.append("appels")
    if cell.route == "local" and profile is not None:
        if run.get("model") != profile["local_model"]:
            errors.append("modèle")
        if run.get("backend") != profile["backend"]:
            errors.append("backend")
        if profile["temperature_pair"]:
            expected = 0.7 if cell.reasoning == "high" else 0.1
            if not _close(run.get("temperature"), expected):
                errors.append("température")
        elif not _is_number(run.get("temperature")):
            errors.append("température")
        if run.get("reasoning_effort") != cell.reasoning:
            errors.append("reasoning_effort")
    elif cell.route == "api":
        if run.get("model") != "mistral-small-2603":
            errors.append("modèle")
        if run.get("backend") != "remote":
            errors.append("backend")
        if not _close(run.get("temperature"), 0.1):
            errors.append("température")
        if run.get("reasoning_effort") != "none":
            errors.append("reasoning_effort")
    else:
        if not run.get("model"):
            errors.append("modèle")
        if run.get("backend") != "remote":
            errors.append("backend")
        if run.get("reasoning_effort") not in {"none", "high", "absent"}:
            errors.append("reasoning_effort")
    if cell.skill == "plan" and run.get("plan_then_execute") is not True:
        errors.append("plan")
    if cell.skill == "poteto":
        models = run.get("subagent_models")
        if not isinstance(models, dict) or not models or any(not value for value in models.values()):
            errors.append("sous-agents")
    if cell.skill == "pocock" and run.get("pocock_path") not in {"scripted", "implement-tdd"}:
        errors.append("pocock")
    return errors


def load_ledger(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("campaign_ledger_under_test", path)
    if spec is None or spec.loader is None:
        raise ImportError(str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ledger_contract(module: Any) -> bool:
    return (
        module.close_day([]) == 0
        and module.close_day([10, 20]) == 30
        and module.close_day([1]) == 2
        and module.version() == 1
    )


def grade(task_id: str, answer: str, module: Any, source_error: str | None, fact: str | None) -> int:
    if task_id == "fait-externe":
        phrase = fact if fact is not None else fact_phrase()
        return int(phrase.casefold() in answer.casefold())
    if task_id == "question":
        return int(_ANSWER_ZERO.search(answer) is not None)
    if source_error or module is None:
        return 0
    if task_id == "bug":
        return int(_ledger_contract(module))
    if task_id == "quelques-lignes":
        kept = module.close_day([]) == 0 and module.close_day([10, 20]) == 30 and module.close_day([1]) == 2
        return int(module.version() == 2 and kept)
    if task_id == "refactor":
        try:
            body = inspect.getsource(module.close_day)
        except (OSError, TypeError):
            return 0
        separate = "% 2" not in body and hasattr(module, "rounded")
        rounded_ok = separate and module.rounded(3) == 4 and module.rounded(4) == 4
        return int(rounded_ok and _ledger_contract(module))
    if task_id in {"flou", "revue"}:
        return int(_ledger_contract(module))
    return 0


def changed_lines_by_path(diff: str) -> dict[str, int]:
    current: str | None = None
    counts: dict[str, int] = {}
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            raw = parts[-1] if parts else ""
            if raw.startswith("b/"):
                raw = raw[2:]
            current = raw.replace("\\", "/")
            counts.setdefault(current, 0)
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            raw = line[4:].split("\t", 1)[0].strip()
            if raw.startswith("b/"):
                raw = raw[2:]
            current = raw.replace("\\", "/")
            counts.setdefault(current, 0)
        elif current and (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---")):
            counts[current] = counts.get(current, 0) + 1
    return counts


def _ignored_path(path: str) -> bool:
    return path in HARNESS_PATHS or path.startswith("campaign/external/")


def _in_scope(task_id: str, path: str) -> bool:
    scope = TASKS[task_id].scope
    if scope == "ledger":
        return path == "campaign/fixture/ledger.py"
    if scope == "fixture-not-tests":
        return path.startswith("campaign/fixture/") and not Path(path).name.startswith("test_")
    return False


def out_of_scope_lines(task_id: str, diff: str) -> int:
    total = 0
    for path, count in changed_lines_by_path(diff).items():
        if _ignored_path(path) or _in_scope(task_id, path):
            continue
        total += count
    return total


def _mcp_scores(task: Task, calls: list[Any]) -> tuple[float | None, float | None]:
    count = len(calls)
    if task.mcp == "required":
        recall = 1.0 if count else 0.0
        precision = 1.0 if count else None
        return recall, precision
    if task.mcp == "forbidden":
        precision = 0.0 if count else 1.0
        return None, precision
    return None, None


def _proof_value(task_id: str, proof: Any) -> float | None:
    if task_id != "bug":
        return None
    if not isinstance(proof, list) or len(proof) < 2:
        return 0.0
    first, second = proof[0], proof[1]
    try:
        same = str(first["command"]).strip() == str(second["command"]).strip()
        failed_then_passed = int(first["exit"]) != 0 and int(second["exit"]) == 0
    except (KeyError, TypeError, ValueError):
        return 0.0
    return 1.0 if same and failed_then_passed else 0.0


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def measure(run: dict[str, Any], ledger_path: Path | None = None, fact: str | None = None) -> dict[str, Any]:
    errors = case_errors(run)
    task_id = str(run.get("task") or "")
    task = TASKS.get(task_id)
    module = None
    source_error: str | None = None
    if task and task.needs_ledger:
        if ledger_path is None or not ledger_path.is_file():
            errors.append("journal")
        else:
            try:
                module = load_ledger(ledger_path)
            except Exception as exc:
                source_error = str(exc)
    auto = 0
    if task is not None and "journal" not in errors:
        try:
            auto = grade(task_id, str(run.get("answer") or ""), module, source_error, fact)
        except (OSError, ValueError) as exc:
            errors.append(f"fait : {exc}")
            auto = 0
        except (TypeError, AttributeError):
            auto = 0
    recall: float | None = None
    precision: float | None = None
    if task is not None and isinstance(run.get("mcp_calls"), list):
        recall, precision = _mcp_scores(task, run["mcp_calls"])
    hors = out_of_scope_lines(task_id, str(run.get("diff") or "")) if task is not None else 0
    reported = run.get("skill_tokens")
    return {
        "valid": not errors,
        "errors": errors,
        "auto_ok": auto,
        "diff_hors_perimetre": hors,
        "mcp_rappel": recall,
        "mcp_precision": precision,
        "preuve": _proof_value(task_id, run.get("proof")) if task is not None else None,
        "questions": _optional_number(run.get("questions")) if _nonnegative(run.get("questions")) else None,
        "question_minutes": _optional_number(run.get("question_minutes"))
        if _nonnegative(run.get("question_minutes"))
        else None,
        "skill_tokens": skill_tokens_files(str(run.get("cell")), task_id) if str(run.get("cell")) in CELLS else 0,
        "skill_tokens_reported": _optional_number(reported) if _nonnegative(reported) else None,
        "task_tokens": _optional_number(run.get("task_tokens")) if _nonnegative(run.get("task_tokens")) else None,
        "minutes": _optional_number(run.get("minutes")) if _nonnegative(run.get("minutes")) else None,
    }


def median(values: list[float]) -> float:
    return float(statistics.median(values))


def comparison(left: list[float], right: list[float]) -> dict[str, Any]:
    """``ne pas choisir`` when the run range is strictly wider than the case gap."""
    empty: dict[str, Any] = {
        "status": "incomplet",
        "spread": None,
        "gap": None,
        "left": None,
        "right": None,
        "reason": "",
    }
    if len(left) < MIN_RUNS or len(right) < MIN_RUNS:
        return empty
    left_m = median(left)
    right_m = median(right)
    gap = abs(left_m - right_m)
    spread = max(max(left) - min(left), max(right) - min(right))
    if spread > gap:
        status = "ne pas choisir"
        reason = "l'écart des runs dépasse l'écart des cases"
    elif left_m == right_m:
        status = "egal"
        reason = ""
    elif left_m > right_m:
        status = "gauche"
        reason = ""
    else:
        status = "droite"
        reason = ""
    return {"status": status, "spread": spread, "gap": gap, "left": left_m, "right": right_m, "reason": reason}


def metric_applies(task_id: str, metric: str) -> bool:
    if metric == "preuve":
        return task_id == "bug"
    if metric == "mcp_rappel":
        return TASKS[task_id].mcp == "required"
    if metric == "mcp_precision":
        return TASKS[task_id].mcp in {"required", "forbidden"}
    return True


def metric_values(runs: list[dict[str, Any]], cell_id: str, task_id: str, metric: str) -> list[float]:
    found: list[float] = []
    for run in runs:
        if run.get("cell") != cell_id or run.get("task") != task_id:
            continue
        metrics = run.get("metrics") or {}
        if not metrics.get("valid"):
            continue
        value = metrics.get(metric)
        if value is None:
            continue
        found.append(float(value))
    return found


def _valid(runs: list[dict[str, Any]], cell_id: str) -> list[dict[str, Any]]:
    return [run for run in runs if run.get("cell") == cell_id and (run.get("metrics") or {}).get("valid")]


def cell_block(cell_id: str, runs: list[dict[str, Any]]) -> str | None:
    """Runs of one cell that are not the same case."""
    cell = CELLS[cell_id]
    rows = _valid(runs, cell_id)
    if not rows:
        return None
    if cell.skill == "poteto":
        maps = [run.get("subagent_models") or {} for run in rows]
        if any(not mapping for mapping in maps):
            return "un run poteto n'a pas de modèle de sous-agent"
        frozen = {json.dumps(mapping, sort_keys=True, ensure_ascii=False) for mapping in maps}
        if len(frozen) != 1:
            return "les modèles des sous-agents diffèrent entre les runs"
    if cell.skill == "pocock":
        if len({run.get("pocock_path") for run in rows}) != 1:
            return "le chemin Pocock diffère entre les runs"
    if cell.reasoning == "noted" and len({run.get("reasoning_effort") for run in rows}) != 1:
        return "reasoning_effort diffère entre les runs"
    if cell.route == "copilot" and len({run.get("model") for run in rows}) != 1:
        return "le nom du modèle Copilot diffère entre les runs"
    if len({run.get("temperature") for run in rows}) != 1:
        return "la température diffère entre les runs"
    return None


def pair_block(left_id: str, right_id: str, runs: list[dict[str, Any]]) -> str | None:
    for cell_id in (left_id, right_id):
        reason = cell_block(cell_id, runs)
        if reason:
            return reason
    if CELLS[left_id].route != "copilot":
        return None
    left_rows = _valid(runs, left_id)
    right_rows = _valid(runs, right_id)
    if not left_rows or not right_rows:
        return None
    if {run.get("model") for run in left_rows} != {run.get("model") for run in right_rows}:
        return "le nom du modèle Copilot n'est pas le même"
    if {run.get("reasoning_effort") for run in left_rows} != {run.get("reasoning_effort") for run in right_rows}:
        return "reasoning_effort n'est pas le même des deux côtés"
    return None


def token_keys(left_id: str, right_id: str) -> tuple[str, str] | None:
    def key(cell_id: str) -> str:
        if CELLS[cell_id].skill == "poteto":
            return "skill_tokens_reported"
        return "skill_tokens"

    left, right = key(left_id), key(right_id)
    if left != right:
        return None
    return left, right


def beats(rows: list[dict[str, Any]], *, lower_is_better: bool) -> str:
    """``oui`` only with a strict win and no task lost. A tie is ``egal``."""
    wins = 0
    losses = 0
    for row in rows:
        if row["status"] == "egal":
            continue
        left_better = row["status"] == ("droite" if lower_is_better else "gauche")
        if left_better:
            wins += 1
        else:
            losses += 1
    if losses:
        return "non"
    if wins:
        return "oui"
    return "egal"


def _empty_roll(status: str, reason: str, decision: str) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "decision": decision,
        "mean_left": None,
        "mean_right": None,
        "spread": None,
        "gap": None,
        "beats": None,
        "rows": [],
    }


def rollup(runs: list[dict[str, Any]], left_id: str, right_id: str, metric: str) -> dict[str, Any]:
    decision = DECISIONS[metric]
    block = pair_block(left_id, right_id, runs)
    if block:
        return _empty_roll("ne pas choisir", block, decision)
    key_left, key_right = metric, metric
    if metric == "skill_tokens":
        keys = token_keys(left_id, right_id)
        if keys is None:
            return _empty_roll("ne pas choisir", "sources de tokens du pack différentes", decision)
        key_left, key_right = keys
    rows: list[dict[str, Any]] = []
    for task in TASK_ORDER:
        if not metric_applies(task.id, metric):
            continue
        left_vals = metric_values(runs, left_id, task.id, key_left)
        right_vals = metric_values(runs, right_id, task.id, key_right)
        if not left_vals and not right_vals:
            continue
        rows.append(comparison(left_vals, right_vals))
    if not rows:
        return _empty_roll("incomplet", "", decision)
    if any(row["status"] == "incomplet" for row in rows):
        rolled = _empty_roll("incomplet", "", decision)
        rolled["rows"] = rows
        return rolled
    if any(row["status"] == "ne pas choisir" for row in rows):
        rolled = _empty_roll("ne pas choisir", "l'écart des runs dépasse l'écart des cases", decision)
        rolled["rows"] = rows
        return rolled
    mean_left = sum(row["left"] for row in rows) / len(rows)
    mean_right = sum(row["right"] for row in rows) / len(rows)
    spreads = [row["spread"] for row in rows if row["spread"] is not None]
    return {
        "status": "lu",
        "reason": "",
        "decision": decision,
        "mean_left": mean_left,
        "mean_right": mean_right,
        "spread": max(spreads) if spreads else 0,
        "gap": abs(mean_left - mean_right),
        "beats": beats(rows, lower_is_better=metric in LOWER_IS_BETTER),
        "rows": rows,
    }


def place_from(auto_roll: dict[str, Any], hors_roll: dict[str, Any]) -> str:
    if auto_roll["status"] != "lu":
        return str(auto_roll["status"])
    if auto_roll["beats"] == "oui":
        return "place"
    if auto_roll["beats"] == "non":
        return "pas de place"
    if hors_roll["status"] != "lu":
        return str(hors_roll["status"])
    if hors_roll["beats"] == "oui":
        return "place"
    return "pas de place"


def one_task(
    runs: list[dict[str, Any]],
    left_id: str,
    right_id: str,
    task_id: str,
    metric: str,
) -> dict[str, Any]:
    block = pair_block(left_id, right_id, runs)
    if block:
        row = comparison([], [])
        row["status"] = "ne pas choisir"
        row["reason"] = block
    else:
        key = metric
        if metric == "skill_tokens":
            keys = token_keys(left_id, right_id)
            key = keys[0] if keys else metric
            if keys is None:
                row = comparison([], [])
                row["status"] = "ne pas choisir"
                row["reason"] = "sources de tokens du pack différentes"
                key = ""
        if key:
            left_vals = metric_values(runs, left_id, task_id, key)
            right_vals = metric_values(runs, right_id, task_id, key)
            if not left_vals and not right_vals:
                row = comparison([], [])
            else:
                row = comparison(left_vals, right_vals)
    row["metric"] = metric
    row["task"] = task_id
    row["decision"] = DECISIONS[metric]
    row["left_id"] = left_id
    row["right_id"] = right_id
    return row


def mcp_choice(auto_row: dict[str, Any], recall: list[float]) -> str:
    status = auto_row["status"]
    if status in {"incomplet", "ne pas choisir"}:
        return status
    if status == "egal":
        return "egal"
    if status == "droite":
        return "sans MCP"
    if len(recall) < MIN_RUNS:
        return "incomplet"
    if median(recall) <= 0:
        return "succès sans appel"
    return "avec MCP"


def mcp_effect(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """No skill, reasoning held. Left is MCP on.

    The choice uses the external-fact task. Precision on the pure-code
    question is reported beside it: a call there is a failure, and it is not
    a reason to turn MCP on.
    """
    rows: list[dict[str, Any]] = []
    for route, on_id, off_id in MCP_PAIRS:
        recall = metric_values(runs, on_id, "fait-externe", "mcp_rappel")
        for task_id in ("fait-externe", "question"):
            for metric in DECISIONS:
                if not metric_applies(task_id, metric):
                    continue
                item = one_task(runs, on_id, off_id, task_id, metric)
                item["route"] = route
                item["choice"] = mcp_choice(item, recall) if metric == "auto_ok" and task_id == "fait-externe" else None
                rows.append(item)
    return rows


def reasoning_effect(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = []
    for metric in DECISIONS:
        item = rollup(runs, "local-mcp-high", "local-mcp", metric)
        item["metric"] = metric
        metrics.append(item)
    return {"note": REASONING_NOTE, "metrics": metrics}


def pack_report(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Layer B. Left is the conduction, right is the checklist."""
    report: list[dict[str, Any]] = []
    for cell_id, place_label in (("local-mcp", "référence"), *((skill, None) for skill in PACK_SKILLS)):
        auto_roll = rollup(runs, cell_id, "checklist", "auto_ok")
        hors_roll = rollup(runs, cell_id, "checklist", "diff_hors_perimetre")
        place = place_label if place_label else place_from(auto_roll, hors_roll)
        metrics = []
        for metric in DECISIONS:
            item = rollup(runs, cell_id, "checklist", metric)
            item["metric"] = metric
            metrics.append(item)
        report.append({"cell": cell_id, "against": "checklist", "place": place, "metrics": metrics})
    return report


def _place_of(packs: list[dict[str, Any]], cell_id: str) -> str:
    for row in packs:
        if row["cell"] == cell_id:
            return str(row["place"])
    return "incomplet"


def interaction_report(runs: list[dict[str, Any]], packs: list[dict[str, Any]]) -> dict[str, Any]:
    inherited = {
        "local-mcp": "référence",
        "checklist": "barre",
        "poteto": _place_of(packs, "poteto"),
        "copilot-poteto": _place_of(packs, "poteto"),
        "copilot-pocock": _place_of(packs, "pocock"),
    }
    cells = []
    for cell_id in ("local-mcp", "checklist", "copilot-poteto", "copilot-pocock", "poteto"):
        cells.append(
            {
                "id": cell_id,
                "mean_auto": _mean_auto(runs, cell_id),
                "place": inherited[cell_id],
            }
        )
    contrasts = []
    for left_id, right_id, pack_id in (
        ("poteto", "copilot-poteto", "poteto"),
        ("pocock", "copilot-pocock", "pocock"),
    ):
        contrasts.append(
            {
                "left": left_id,
                "right": right_id,
                "place": inherited[pack_id if pack_id != "pocock" else "copilot-pocock"],
                "note": INTERACTION_NOTE,
                "auto_ok": {**rollup(runs, left_id, right_id, "auto_ok"), "metric": "auto_ok"},
                "minutes": {**rollup(runs, left_id, right_id, "minutes"), "metric": "minutes"},
            }
        )
    return {"cells": cells, "contrasts": contrasts}


def _mean_auto(runs: list[dict[str, Any]], cell_id: str) -> float | None:
    if cell_block(cell_id, runs):
        return None
    medians: list[float] = []
    for task in TASK_ORDER:
        values = metric_values(runs, cell_id, task.id, "auto_ok")
        if len(values) < MIN_RUNS:
            return None
        medians.append(median(values))
    return sum(medians) / len(medians)


def duplicate_gate(runs: list[dict[str, Any]]) -> str | None:
    seen: set[tuple[Any, Any, Any]] = set()
    for run in runs:
        key = (run.get("cell"), run.get("task"), run.get("run"))
        if key in seen:
            return f"run en double : {key[0]} {key[1]} {key[2]}"
        seen.add(key)
    return None


def _valid_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in runs if (run.get("metrics") or {}).get("valid")]


def commit_gate(runs: list[dict[str, Any]]) -> str | None:
    commits = {run.get("commit") for run in _valid_runs(runs)}
    commits.discard("")
    commits.discard(None)
    if len(commits) > 1:
        return "les runs valides ne partagent pas le même commit"
    return None


def folder_gate(runs: list[dict[str, Any]]) -> str | None:
    """One host and one local model per decide folder. The two machines stay apart."""
    valid = _valid_runs(runs)
    hosts = {run.get("host") for run in valid if run.get("host")}
    if len(hosts) > 1:
        return "deux hôtes dans le même dossier"
    models = {run.get("model") for run in valid if run.get("route") == "local" and run.get("model")}
    if len(models) > 1:
        return "deux modèles locaux dans le même dossier"
    return None


def folder_identity(runs: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    valid = _valid_runs(runs)
    hosts = {run.get("host") for run in valid if run.get("host")}
    models = {run.get("model") for run in valid if run.get("route") == "local" and run.get("model")}
    host = next(iter(hosts)) if len(hosts) == 1 else None
    model = next(iter(models)) if len(models) == 1 else None
    return host, model


def decide(runs: list[dict[str, Any]]) -> dict[str, Any]:
    host, local_model = folder_identity(runs)
    gate = duplicate_gate(runs) or commit_gate(runs) or folder_gate(runs)
    if gate:
        return {
            "gate": gate,
            "host": host,
            "local_model": local_model,
            "mcp": [],
            "reasoning": {},
            "packs": [],
            "interaction": {},
        }
    packs = pack_report(runs)
    return {
        "gate": None,
        "host": host,
        "local_model": local_model,
        "mcp": mcp_effect(runs),
        "reasoning": reasoning_effect(runs),
        "packs": packs,
        "interaction": interaction_report(runs, packs),
    }


def _fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.3g}"


def _fmt_roll(item: dict[str, Any]) -> str:
    name = item.get("metric", "")
    decision = item.get("decision", "")
    status = item["status"]
    if status != "lu":
        reason = item.get("reason") or ""
        extra = f" {reason}." if reason else ""
        return f"  {name} : {status}.{extra} Décision : {decision}"
    return (
        f"  {name} : {_fmt_num(item['mean_left'])} contre {_fmt_num(item['mean_right'])}, "
        f"écart max des runs {_fmt_num(item['spread'])}, écart des moyennes {_fmt_num(item['gap'])}. "
        f"Décision : {decision}"
    )


def _fmt_one(item: dict[str, Any]) -> str:
    decision = item["decision"]
    status = item["status"]
    choice = ""
    if item.get("choice"):
        choice = f" Choix : {item['choice']}."
    if status not in {"gauche", "droite", "egal"}:
        reason = item.get("reason") or ""
        extra = f" {reason}." if reason else ""
        head = f"  {item['route']} {item['task']} {item['metric']} : {status}.{extra}{choice}"
        return f"{head} Décision : {decision}"
    head = f"  {item['route']} {item['task']} {item['metric']}"
    gap = f"écart des runs {_fmt_num(item['spread'])}, écart des cases {_fmt_num(item['gap'])}"
    return f"{head} : {_fmt_num(item['left'])} contre {_fmt_num(item['right'])}, {gap}.{choice} Décision : {decision}"


def format_report(report: dict[str, Any]) -> str:
    lines = []
    if report.get("host") or report.get("local_model"):
        host = report.get("host") or "—"
        model = report.get("local_model") or "—"
        lines.append(f"Dossier : hôte {host}, modèle local {model}.")
    lines.extend(
        [
        "Lecture : effet MCP (fait externe, même modèle, sans skill), puis effet du pack, puis l'interaction.",
        "Une tâche perdue sur l'auto refuse la place.",
        "Un écart de runs plus grand que l'écart des cases : ne pas choisir.",
        "Le pack qui ne bat pas la checklist ne gagne pas sa place.",
        ]
    )
    if report.get("gate"):
        lines.append(f"Porte : {report['gate']}. Ne pas choisir.")
        return "\n".join(lines) + "\n"
    lines.append("A. Effet MCP, tâche fait-externe. Gauche = avec MCP.")
    for item in report["mcp"]:
        lines.append(_fmt_one(item))
    reasoning = report["reasoning"]
    lines.append("A. Dédoublement reasoning_effort, local avec MCP. Gauche = high.")
    lines.append(reasoning["note"])
    for item in reasoning["metrics"]:
        lines.append(_fmt_roll(item))
    lines.append("B. Effet du pack, même modèle, MCP allumé. Gauche = la conduite, droite = la checklist.")
    for row in report["packs"]:
        lines.append(f"{row['cell']} : {row['place']}")
        for item in row["metrics"]:
            lines.append(_fmt_roll(item))
    lines.append("C. Interaction. La place est celle de B.")
    interaction = report["interaction"]
    for cell in interaction.get("cells", []):
        lines.append(f"  {cell['id']} : auto {_fmt_num(cell['mean_auto'])}, {cell['place']}")
    for contrast in interaction.get("contrasts", []):
        lines.append(f"{contrast['left']} contre {contrast['right']} : {contrast['place']}")
        lines.append(contrast["note"])
        lines.append(_fmt_roll(contrast["auto_ok"]))
        lines.append(_fmt_roll(contrast["minutes"]))
    return "\n".join(lines) + "\n"


def plan_text() -> str:
    tasks = len(TASK_ORDER)
    cells = len(CELL_ORDER)
    runs = cells * tasks * MIN_RUNS
    lines = [
        "Hôte final : GMKtec EVO-X5 Pro, modèle local mistral-small-4.",
        "Répétition sur cette 4080 : --host 4080, modèle local Qwen2.5-Coder-14B-Instruct-Q5_K_M.",
        "Chaque machine a son dossier. decide refuse un mélange.",
        "API : mistral-small-2603. Copilot : le nom affiché, noté sur le run.",
        f"Cases uniques : {cells}. Tâches : {tasks}. Runs minimum : {runs}.",
        "A réutilise rien. B réutilise local-mcp. C réutilise local-mcp, checklist et poteto.",
        "Nouvelles cases de C : copilot-poteto, copilot-pocock.",
        "Dédoublement reasoning none/high : seulement local-mcp. B et C restent sur none.",
        "Si l'écart des runs dépasse l'écart des cases, ne pas choisir.",
        "Le pack qui ne bat pas la checklist ne gagne pas sa place. C ne la rend pas.",
        "Tâches : " + ", ".join(task.id for task in TASK_ORDER),
        "Décisions :",
    ]
    for metric, decision in DECISIONS.items():
        lines.append(f"  {metric} — {decision}")
    return "\n".join(lines) + "\n"


def _load_runs(directory: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("run.json")):
        runs.append(json.loads(path.read_text(encoding="utf-8")))
    return runs


def _print_measure(metrics: dict[str, Any]) -> None:
    if metrics["errors"]:
        print("écarts de case : " + ", ".join(metrics["errors"]))
    for metric, decision in DECISIONS.items():
        if metric == "skill_tokens":
            value = metrics.get("skill_tokens")
            reported = metrics.get("skill_tokens_reported")
            print(f"{metric} : fichiers {value}, client {reported}. Décision : {decision}")
            continue
        print(f"{metric} : {metrics.get(metric)}. Décision : {decision}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("plan", help="print the matrix, the run count, and the decision each number changes")

    prep = sub.add_parser("prepare", help="detach a worktree at a commit and write the run card")
    prep.add_argument("--cell", required=True, choices=tuple(CELLS))
    prep.add_argument("--task", required=True, choices=tuple(TASKS))
    prep.add_argument("--run", required=True, type=int)
    prep.add_argument("--commit", required=True)
    prep.add_argument("--host", choices=tuple(HOSTS), default="evo-x5")

    score = sub.add_parser("score", help="fill metrics in a run.json")
    score.add_argument("--run", required=True, type=Path)
    score.add_argument("--worktree", type=Path)

    decide_parser = sub.add_parser("decide", help="read run.json files and apply the three-layer rule")
    decide_parser.add_argument("directory", type=Path)

    args = parser.parse_args(argv)
    if args.command == "plan":
        print(plan_text(), end="")
        return 0
    if args.command == "prepare":
        return prepare(args.cell, args.task, args.run, args.commit, args.host)
    if args.command == "score":
        run_path = args.run
        payload = json.loads(run_path.read_text(encoding="utf-8"))
        ledger = None if args.worktree is None else args.worktree / "campaign" / "fixture" / "ledger.py"
        payload["metrics"] = measure(payload, ledger)
        run_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _print_measure(payload["metrics"])
        return 0 if payload["metrics"]["valid"] else 1
    report = decide(_load_runs(args.directory))
    print(format_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
