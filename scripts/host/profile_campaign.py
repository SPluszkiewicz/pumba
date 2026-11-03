#!/usr/bin/env python3
"""RTX 6000 profile campaign from docs/guides/rtx6000-scenarios.md.

Prints which profiles match `nvidia-smi` and, with `--run`, applies each one,
recreates `llm`, and sends S0–S5 to the local agent. This does not run in CI:
it needs the GPU machine, the GGUF files, and a registered `sample` checkout.

    python scripts/host/profile_campaign.py
    python scripts/host/profile_campaign.py --vram 24576
    python scripts/host/profile_campaign.py --run --workspace D:/rest/local_llm/sample

`--run` copies `.env` to `state/profile-campaign.env.bak` first. The report
is `state/profile-campaign.json`. Neither file is committed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
REPORT_PATH = REPO_ROOT / "state" / "profile-campaign.json"
ENV_BACKUP = REPO_ROOT / "state" / "profile-campaign.env.bak"
SAMPLE = REPO_ROOT / "sample"

# Below this, the card is the 16 Go class and this campaign does not apply.
_VRAM_24G_MIN = 20_000
_VRAM_48G_MIN = 40_000

SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "id": "S1",
        "prompt": (
            "Lance uniquement le cas ctest known_bug. Utilise les outils. Ne devine pas le résultat."
        ),
    },
    {
        "id": "S2",
        "prompt": (
            "Le code de sample montre-t-il que 2+3*4 vaut 14 ? "
            "Cite path:ligne. Si le code ne le montre pas, dis-le."
        ),
    },
    {
        "id": "S3",
        "prompt": "Où est déclaré Zorglub42 ? Si le dépôt ne le contient pas, dis-le.",
    },
    {
        "id": "S4",
        "prompt": (
            "Propose un seul diff unifié pour que le test "
            "« known_bug: multiplication binds tighter than addition » passe. "
            "Ne modifie pas le test. N'applique pas le diff."
        ),
    },
    {
        "id": "S5",
        "prompt": (
            "Ajoute int Version() qui retourne 1 : déclaration dans src/calculator.hpp, "
            "définition dans src/calculator.cpp. Un seul diff unifié. N'applique pas le diff."
        ),
    },
)

_ABSENCE = (
    "ne le contient pas",
    "n'existe pas",
    "introuvable",
    "does not",
    "not in the",
    "no such",
    "absent",
)
_SOURCE_PATH = re.compile(r"[\w./\\-]+\.(?:cpp|hpp|h|cmake)\b", re.IGNORECASE)
_DIFF_FENCE = re.compile(r"```(?:diff)?[^\n]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def profiles_for_vram(total_mib: int) -> list[str]:
    """Profiles worth comparing on a card of this size. Empty on 16 Go."""
    if total_mib < _VRAM_24G_MIN:
        return []
    if total_mib < _VRAM_48G_MIN:
        return ["24g", "24g-30b"]
    return ["48g", "48g-30b", "48g-32b"]


def looks_spilled(used_mib: int, weight_bytes: int) -> bool:
    """True when GPU memory is far below the GGUF size (layers left on the CPU)."""
    if weight_bytes <= 0 or used_mib < 0:
        return False
    weight_mib = weight_bytes / (1024 * 1024)
    return used_mib < weight_mib * 0.75


def parse_gpu_mib(csv_line: str) -> tuple[int, int] | None:
    """`used, total` from `nvidia-smi --query-gpu=memory.used,memory.total`."""
    parts = [part.strip() for part in csv_line.split(",")]
    if len(parts) < 2:
        return None
    try:
        return int(float(parts[0])), int(float(parts[1]))
    except ValueError:
        return None


def loaded_model_ids(payload: Any) -> list[str]:
    """Ids reported loaded by llama-server (OpenAI list or router list)."""
    rows: list[Any] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        models = payload.get("models")
        if isinstance(data, list):
            rows = data
        elif isinstance(models, list):
            rows = models
    elif isinstance(payload, list):
        rows = payload
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = str(row.get("id") or row.get("name") or "")
        if not model_id:
            continue
        status = row.get("status")
        if isinstance(status, dict):
            state = str(status.get("value") or "")
        elif isinstance(status, str):
            state = status
        else:
            state = "loaded"
        if state in {"", "loaded"}:
            found.append(model_id.removesuffix(".gguf"))
    return found


def extract_diff(answer: str) -> str | None:
    """First fenced block that looks like a unified diff."""
    for block in _DIFF_FENCE.findall(answer):
        text = block.strip("\n")
        if text.startswith("diff ") or text.startswith("---") or text.startswith("@@") or "\n@@" in text:
            return text + "\n"
    return None


def diff_paths(diff: str) -> set[str]:
    paths: set[str] = set()
    for line in diff.splitlines():
        raw = ""
        if line.startswith("diff --git "):
            parts = line.split()
            raw = parts[-1] if parts else ""
        elif line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            raw = line[4:].split("\t", 1)[0].strip()
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        if raw:
            paths.add(raw.replace("\\", "/"))
    return paths


def score_scenario(
    scenario_id: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    *,
    diff_applies: bool | None = None,
) -> str:
    """`ok` or `raté`. S4/S5 need `diff_applies` from `git apply --check`."""
    if scenario_id == "S1":
        for call in tool_calls:
            if call.get("name") != "test":
                continue
            blob = json.dumps(call.get("arguments", {}), ensure_ascii=False).lower()
            if "known_bug" in blob:
                return "ok"
        return "raté"
    if scenario_id == "S2":
        text = answer.replace("\\", "/")
        if "calculator_test.cpp" in text and "calculator.cpp" in text:
            return "ok"
        return "raté"
    if scenario_id == "S3":
        lowered = answer.lower()
        if _SOURCE_PATH.search(answer):
            return "raté"
        if any(phrase in lowered for phrase in _ABSENCE):
            return "ok"
        return "raté"
    diff = extract_diff(answer)
    if diff is None or diff_applies is not True:
        return "raté"
    paths = diff_paths(diff)
    if scenario_id == "S4":
        touches_impl = any(path.endswith("src/calculator.cpp") for path in paths)
        touches_test = any("calculator_test.cpp" in path for path in paths)
        return "ok" if touches_impl and not touches_test else "raté"
    if scenario_id == "S5":
        has_hpp = any(path.endswith("src/calculator.hpp") for path in paths)
        has_cpp = any(path.endswith("src/calculator.cpp") for path in paths)
        if has_hpp and has_cpp and "Version" in diff:
            return "ok"
        return "raté"
    return "raté"


def apply_check(diff: str, root: Path) -> tuple[bool, str]:
    """`git apply --check` against `root`. Does not modify the tree."""
    if shutil.which("git") is None:
        return False, "git is not on PATH"
    proc = subprocess.run(
        ["git", "apply", "--check", "--unsafe-paths", "-"],
        input=diff,
        text=True,
        capture_output=True,
        cwd=root,
        check=False,
    )
    detail = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode == 0, detail


def _get_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, body: dict[str, Any], timeout: float, bearer: str) -> Any:
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _read_bearer(env_text: str) -> str:
    for line in env_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("AGENT_BEARER_TOKEN="):
            return stripped.split("=", 1)[1].strip()
    return ""


def _deploy() -> Any:
    host = REPO_ROOT / "scripts" / "host"
    if str(host) not in sys.path:
        sys.path.insert(0, str(host))
    return importlib.import_module("deploy")


def _profile_model_file(name: str) -> tuple[str, Path]:
    deploy = _deploy()
    overlay = deploy._parse_env_file(deploy.GPU_PROFILE_DIR / f"{name}.env")
    filename = Path(overlay["LLM_MODEL_PATH"]).name
    return filename.removesuffix(".gguf"), REPO_ROOT / "models" / filename


def query_nvidia_smi() -> tuple[int, int] | None:
    if shutil.which("nvidia-smi") is None:
        return None
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return None
    return parse_gpu_mib(line[0])


def _wait_healthy(llm_url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _get_json(f"{llm_url.rstrip('/')}/health", timeout=5)
            return True
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            time.sleep(5)
    return False


def _recreate(services: list[str], *, build_embedder: bool) -> None:
    deploy = _deploy()
    deploy._activate_runtime(["profile-campaign"])
    if build_embedder:
        deploy._run(deploy._compose_cmd("build", "embedder"), cwd=str(REPO_ROOT))
    deploy._run(
        deploy._compose_cmd("up", "-d", "--no-deps", "--force-recreate", *services),
        cwd=str(REPO_ROOT),
    )


def _run_profile(
    name: str,
    *,
    workspace: str,
    agent_url: str,
    llm_url: str,
    bearer: str,
    load_timeout: float,
    chat_timeout: float,
) -> dict[str, Any]:
    stem, weight = _profile_model_file(name)
    row: dict[str, Any] = {"profile": name, "model": stem, "scenarios": {}}
    if not weight.is_file():
        extra = ""
        fetch = _deploy().PROFILE_EXTRA.get(name)
        if fetch:
            extra = f" python scripts/deploy.py fetch-extra {name}"
        row["status"] = "éliminé"
        row["reason"] = f"gguf absent: {weight.name}.{extra}".rstrip()
        return row

    before = ""
    if ENV_FILE.is_file():
        before = ENV_FILE.read_text(encoding="utf-8")
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "host" / "deploy.py"), "gpu-profile", name],
        cwd=REPO_ROOT,
        check=True,
    )
    embedder_changed = "EMBEDDER_GPU_ARG=" in before and (
        _env_value(before, "EMBEDDER_GPU_ARG") != _env_value(ENV_FILE.read_text(encoding="utf-8"), "EMBEDDER_GPU_ARG")
    )
    services = ["llm", "embedder", "reranker"] if embedder_changed else ["llm"]
    _recreate(services, build_embedder=embedder_changed)

    if not _wait_healthy(llm_url, load_timeout):
        row["status"] = "éliminé"
        row["reason"] = "llm health timeout"
        return row

    try:
        catalog = _get_json(f"{llm_url.rstrip('/')}/v1/models", timeout=30)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        row["status"] = "éliminé"
        row["reason"] = f"models endpoint: {exc}"
        return row
    loaded = loaded_model_ids(catalog)
    row["loaded"] = loaded
    if stem not in loaded:
        row["status"] = "éliminé"
        row["reason"] = f"loaded {loaded or ['none']}, expected {stem}"
        return row

    gpu = query_nvidia_smi()
    if gpu is not None:
        row["used_mib"] = gpu[0]
        row["total_mib"] = gpu[1]
        row["spilled"] = looks_spilled(gpu[0], weight.stat().st_size)
        if row["spilled"]:
            row["status"] = "éliminé"
            row["reason"] = "gpu memory far below the gguf size"
            return row

    started = time.monotonic()
    try:
        ping = _post_json(
            f"{llm_url.rstrip('/')}/v1/chat/completions",
            {
                "model": stem,
                "messages": [{"role": "user", "content": "Réponds exactement : pong"}],
                "max_tokens": 16,
                "temperature": 0,
            },
            chat_timeout,
            bearer="",
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        row["status"] = "éliminé"
        row["reason"] = f"pong: {exc}"
        return row
    row["s0_seconds"] = round(time.monotonic() - started, 2)
    timings = ping.get("timings") if isinstance(ping, dict) else None
    if isinstance(timings, dict) and "predicted_per_second" in timings:
        row["tokens_per_second"] = timings["predicted_per_second"]
    row["status"] = "ok"

    for scenario in SCENARIOS:
        scenario_started = time.monotonic()
        try:
            body = _post_json(
                f"{agent_url.rstrip('/')}/chat",
                {"query": scenario["prompt"], "workspace": workspace, "tools": True},
                chat_timeout,
                bearer,
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            row["scenarios"][scenario["id"]] = {"status": "raté", "error": str(exc)}
            continue
        answer = str(body.get("answer") or "")
        tool_calls = body.get("tool_calls") if isinstance(body.get("tool_calls"), list) else []
        applies: bool | None = None
        diff = extract_diff(answer)
        if scenario["id"] in {"S4", "S5"} and diff is not None:
            applies, detail = apply_check(diff, SAMPLE)
            if not applies:
                row.setdefault("diff_errors", {})[scenario["id"]] = detail
        status = score_scenario(scenario["id"], answer, tool_calls, diff_applies=applies)
        row["scenarios"][scenario["id"]] = {
            "status": status,
            "seconds": round(time.monotonic() - scenario_started, 2),
        }
    return row


def _env_value(text: str, key: str) -> str:
    prefix = f"{key}="
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split("=", 1)[1].strip()
    return ""


def _print_plan(profiles: list[str], total_mib: int | None) -> None:
    if total_mib is None:
        print("[campaign] nvidia-smi introuvable. Relance avec --vram <MiB> ou --profiles.", flush=True)
        return
    print(f"[campaign] memory.total ≈ {total_mib} MiB", flush=True)
    if not profiles:
        print("[campaign] Carte 16 Go : cette campagne ne s'y lance pas. Rester sur le profil 16g.", flush=True)
        return
    print("[campaign] Profils : " + ", ".join(profiles), flush=True)
    print("[campaign] Plan seulement. Ajoute --run pour appliquer les profils et interroger l'agent.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vram", type=int, default=None, help="memory.total in MiB, if nvidia-smi is not used")
    parser.add_argument("--profiles", default="", help="comma-separated override, e.g. 24g,24g-30b")
    parser.add_argument("--run", action="store_true", help="apply profiles, recreate llm, send S0–S5")
    parser.add_argument("--workspace", default="", help="registered host path of sample (required with --run)")
    parser.add_argument("--agent", default="http://127.0.0.1:8090")
    parser.add_argument("--llm", default="http://127.0.0.1:8080")
    parser.add_argument("--load-timeout", type=float, default=300.0)
    parser.add_argument("--chat-timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    gpu = query_nvidia_smi()
    total = args.vram if args.vram is not None else (gpu[1] if gpu else None)
    if args.profiles.strip():
        profiles = [part.strip() for part in args.profiles.split(",") if part.strip()]
    else:
        profiles = profiles_for_vram(total) if total is not None else []

    if not args.run:
        _print_plan(profiles, total)
        return 0

    if not profiles:
        print("[campaign] FATAL: aucun profil. Donne --vram ou --profiles.", file=sys.stderr)
        return 1
    if not args.workspace.strip():
        print("[campaign] FATAL: --workspace est le chemin hôte de sample, déjà enregistré.", file=sys.stderr)
        return 1
    if not ENV_FILE.is_file():
        print(f"[campaign] FATAL: {ENV_FILE} est absent.", file=sys.stderr)
        return 1

    ENV_BACKUP.parent.mkdir(parents=True, exist_ok=True)
    ENV_BACKUP.write_text(ENV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    bearer = _read_bearer(ENV_BACKUP.read_text(encoding="utf-8"))
    if not bearer:
        print("[campaign] FATAL: AGENT_BEARER_TOKEN manquant dans .env.", file=sys.stderr)
        return 1

    rows: list[dict[str, Any]] = []
    try:
        for name in profiles:
            print(f"[campaign] {name}", flush=True)
            try:
                rows.append(
                    _run_profile(
                        name,
                        workspace=args.workspace,
                        agent_url=args.agent,
                        llm_url=args.llm,
                        bearer=bearer,
                        load_timeout=args.load_timeout,
                        chat_timeout=args.chat_timeout,
                    )
                )
            except Exception as exc:
                rows.append({"profile": name, "status": "éliminé", "reason": str(exc), "scenarios": {}})
    finally:
        ENV_FILE.write_text(ENV_BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
        print(
            "[campaign] .env restauré depuis la copie. Le conteneur llm est encore sur le dernier profil lancé.\n"
            "Pour le garder : python scripts/deploy.py gpu-profile <nom>, puis recrée llm.",
            flush=True,
        )

    REPORT_PATH.write_text(json.dumps({"profiles": rows}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[campaign] rapport : {REPORT_PATH}", flush=True)
    for row in rows:
        scores = " ".join(f"{key}={value.get('status')}" for key, value in row.get("scenarios", {}).items())
        print(f"  {row['profile']}: {row.get('status')} {row.get('reason', '')} {scores}".rstrip(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
