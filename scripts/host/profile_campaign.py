#!/usr/bin/env python3
"""Profile campaign from docs/notes/verification/rtx6000-scenarios.md and docs/guides/evo-x5.md.

Prints which CUDA profiles match `nvidia-smi`. With `--run`, applies each one
and sends a ping plus the task prompts to the local agent. A CUDA profile
recreates the `llm` container. `evo-x5` starts llama-server on the host and
does not recreate that container. This does not run in CI: it needs the
machine, the GGUF files, and a registered checkout.

    python scripts/host/profile_campaign.py
    python scripts/host/profile_campaign.py --vram 24576
    python scripts/host/profile_campaign.py --run --workspace D:/rest/pumba/sample
    python scripts/host/profile_campaign.py --profiles evo-x5
    python scripts/host/profile_campaign.py --run --profiles evo-x5 --workspace D:/path/to/the/checkout

`--run` copies `.env` to `state/profile-campaign.env.bak` first. The report
is `state/profile-campaign.json`. Neither file is committed. Edit
`scripts/host/evo_campaign_prompts.json` for the EVO tasks. Ids `S1`–`S5`
keep the sample rubric. Any other id records the clock, the server counters,
and the answer.
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
EVO_PROMPTS = Path(__file__).resolve().parent / "evo_campaign_prompts.json"
_SAMPLE_IDS = frozenset({"S1", "S2", "S3", "S4", "S5"})
_CHAT_TIMING_KEYS = (
    "prompt_ms",
    "prompt_per_second",
    "predicted_n",
    "predicted_ms",
    "predicted_per_second",
)
_METRIC_NAMES = {
    "llamacpp:prompt_tokens_total": "prompt_tokens",
    "llamacpp:prompt_seconds_total": "prompt_seconds",
    "llamacpp:tokens_predicted_total": "predicted_tokens",
    "llamacpp:tokens_predicted_seconds_total": "predicted_seconds",
}

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


def outcome(
    scenario_id: str,
    answer: str,
    tool_calls: list[dict[str, Any]],
    *,
    diff_applies: bool | None = None,
) -> str:
    """Sample rubric for S1–S5. Any other id only records that the call returned."""
    if scenario_id not in _SAMPLE_IDS:
        return "mesuré"
    return score_scenario(scenario_id, answer, tool_calls, diff_applies=diff_applies)


def load_prompts(path: Path) -> tuple[dict[str, str], ...]:
    """JSON list of `{id, prompt}`. Ids are unique and non-empty."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[campaign] FATAL: prompts illisibles ({path}): {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise SystemExit(f"[campaign] FATAL: {path} doit être une liste non vide.")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise SystemExit(f"[campaign] FATAL: entrée de prompt invalide dans {path}.")
        ident = str(item.get("id") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not ident or not prompt:
            raise SystemExit(f"[campaign] FATAL: chaque prompt a un id et un texte ({path}).")
        if ident in seen:
            raise SystemExit(f"[campaign] FATAL: id de prompt en double : {ident}.")
        seen.add(ident)
        rows.append({"id": ident, "prompt": prompt})
    return tuple(rows)


def parse_llama_metrics(text: str) -> dict[str, float]:
    """Sum llama-server Prometheus counters. Labels on one name are added together."""
    totals = {name: 0.0 for name in _METRIC_NAMES}
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition(" ")
        base = name.split("{", 1)[0]
        if base not in totals or not rest:
            continue
        try:
            totals[base] += float(rest.split()[0])
        except ValueError:
            continue
        seen.add(base)
    return {name: totals[name] for name in seen}


def metric_timings(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """Counter deltas for one request, plus the rates S0 already reads from `timings`."""
    delta: dict[str, float] = {}
    for name, short in _METRIC_NAMES.items():
        if name in before and name in after:
            delta[short] = round(after[name] - before[name], 4)
    prompt_s = delta.get("prompt_seconds")
    prompt_n = delta.get("prompt_tokens")
    predicted_s = delta.get("predicted_seconds")
    predicted_n = delta.get("predicted_tokens")
    if prompt_s and prompt_s > 0 and prompt_n is not None:
        delta["prompt_ms"] = round(prompt_s * 1000, 1)
        delta["prompt_per_second"] = round(prompt_n / prompt_s, 3)
    if predicted_s and predicted_s > 0 and predicted_n is not None:
        delta["predicted_n"] = predicted_n
        delta["predicted_per_second"] = round(predicted_n / predicted_s, 3)
    return delta


def chat_timings(payload: Any) -> dict[str, Any]:
    """Fields llama-server puts on `POST /v1/chat/completions`."""
    timings = payload.get("timings") if isinstance(payload, dict) else None
    if not isinstance(timings, dict):
        return {}
    return {key: timings[key] for key in _CHAT_TIMING_KEYS if key in timings}


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


def _get_text(url: str, timeout: float) -> str:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


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
    """Stem of the GGUF, and its path on the host. Keeps a subdirectory under `models/`."""
    deploy = _deploy()
    overlay = deploy._parse_env_file(deploy.GPU_PROFILE_DIR / f"{name}.env")
    relative = overlay["LLM_MODEL_PATH"].removeprefix("/models/").lstrip("/")
    return Path(relative).name.removesuffix(".gguf"), REPO_ROOT / "models" / relative


def _profile_is_host(name: str) -> bool:
    deploy = _deploy()
    overlay = deploy._parse_env_file(deploy.GPU_PROFILE_DIR / f"{name}.env")
    return overlay.get("LLM_SERVE") == "host"


def _published_model_id(name: str, stem: str) -> str:
    """Id `GET /v1/models` should list. The host server publishes the alias, not the shard stem."""
    if not _profile_is_host(name):
        return stem
    import evo_x5_serve as serve

    return serve.ALIAS


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
    deploy._require_podman()
    if build_embedder:
        deploy._run(["podman", "compose", "build", "embedder"], cwd=str(REPO_ROOT))
    deploy._run(
        ["podman", "compose", "up", "-d", "--no-deps", "--force-recreate", *services],
        cwd=str(REPO_ROOT),
    )


def _snapshot_metrics(llm_url: str, model: str) -> dict[str, float]:
    """Counters around one request. Empty when `/metrics` does not answer."""
    from urllib.parse import quote

    base = llm_url.rstrip("/")
    for url in (f"{base}/metrics", f"{base}/metrics?model={quote(model)}"):
        try:
            return parse_llama_metrics(_get_text(url, timeout=10))
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    return {}


def _run_profile(
    name: str,
    *,
    workspace: str,
    agent_url: str,
    llm_url: str,
    bearer: str,
    load_timeout: float,
    chat_timeout: float,
    scenarios: tuple[dict[str, str], ...] = SCENARIOS,
) -> dict[str, Any]:
    host = _profile_is_host(name)
    stem, weight = _profile_model_file(name)
    published = _published_model_id(name, stem)
    row: dict[str, Any] = {"profile": name, "model": published, "scenarios": {}}
    if not weight.is_file():
        extra = ""
        fetch = _deploy().PROFILE_EXTRA.get(name)
        if fetch:
            extra = f" python scripts/deploy.py fetch-extra {name}"
        row["status"] = "éliminé"
        row["reason"] = f"gguf absent: {weight.name}.{extra}".rstrip()
        return row
    if host:
        import evo_x5_serve as serve

        missing = serve.missing_shards(weight)
        if missing:
            listed = ", ".join(path.name for path in missing)
            row["status"] = "éliminé"
            row["reason"] = f"shards absents: {listed}. python scripts/deploy.py fetch-extra {name}"
            return row

    before = ""
    if ENV_FILE.is_file():
        before = ENV_FILE.read_text(encoding="utf-8")
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "deploy.py"), "gpu-profile", name],
        cwd=REPO_ROOT,
        check=True,
    )
    embedder_changed = "EMBEDDER_GPU_ARG=" in before and (
        _env_value(before, "EMBEDDER_GPU_ARG") != _env_value(ENV_FILE.read_text(encoding="utf-8"), "EMBEDDER_GPU_ARG")
    )
    load_started = time.monotonic()
    if host:
        if embedder_changed:
            _recreate(["embedder", "reranker"], build_embedder=True)
        _recreate(["agent"], build_embedder=False)
        deploy = _deploy()
        deploy._stop_llm_container()
        try:
            deploy._start_host_llm(timeout=max(int(load_timeout), int(deploy.HOST_LLM_READY_TIMEOUT)))
        except SystemExit as exc:
            row["status"] = "éliminé"
            row["reason"] = str(exc.code if exc.code else exc)
            return row
    else:
        services = ["llm", "embedder", "reranker"] if embedder_changed else ["llm"]
        _recreate(services, build_embedder=embedder_changed)

    if not _wait_healthy(llm_url, load_timeout):
        row["status"] = "éliminé"
        row["reason"] = "llm health timeout"
        return row
    if host and not _wait_healthy(agent_url, load_timeout):
        row["status"] = "éliminé"
        row["reason"] = "agent health timeout"
        return row
    row["load_seconds"] = round(time.monotonic() - load_started, 2)

    try:
        catalog = _get_json(f"{llm_url.rstrip('/')}/v1/models", timeout=30)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        row["status"] = "éliminé"
        row["reason"] = f"models endpoint: {exc}"
        return row
    loaded = loaded_model_ids(catalog)
    row["loaded"] = loaded
    matched = next((item for item in (published, stem) if item in loaded), "")
    if not matched:
        row["status"] = "éliminé"
        row["reason"] = f"loaded {loaded or ['none']}, expected {published}"
        return row

    if not host:
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
                "model": matched,
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
    kept = chat_timings(ping)
    if "predicted_per_second" in kept:
        row["tokens_per_second"] = kept["predicted_per_second"]
    if kept:
        row["s0_timings"] = kept
    row["status"] = "ok"

    checkout = Path(workspace)
    for scenario in scenarios:
        before_metrics = _snapshot_metrics(llm_url, matched)
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
            applies, detail = apply_check(diff, checkout)
            if not applies:
                row.setdefault("diff_errors", {})[scenario["id"]] = detail
        status = outcome(scenario["id"], answer, tool_calls, diff_applies=applies)
        entry: dict[str, Any] = {
            "status": status,
            "seconds": round(time.monotonic() - scenario_started, 2),
            "answer": answer,
        }
        timed = metric_timings(before_metrics, _snapshot_metrics(llm_url, matched))
        if timed:
            entry["timings"] = timed
        row["scenarios"][scenario["id"]] = entry
    return row


def _env_value(text: str, key: str) -> str:
    prefix = f"{key}="
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split("=", 1)[1].strip()
    return ""


def _print_plan(profiles: list[str], total_mib: int | None, prompts_path: Path | None = None) -> None:
    if total_mib is None and not profiles:
        print("[campaign] nvidia-smi introuvable. Relance avec --vram <MiB> ou --profiles.", flush=True)
        return
    if total_mib is not None:
        print(f"[campaign] memory.total ≈ {total_mib} MiB", flush=True)
    if not profiles:
        print("[campaign] Carte 16 Go : cette campagne ne s'y lance pas. Rester sur le profil 16g.", flush=True)
        return
    print("[campaign] Profils : " + ", ".join(profiles), flush=True)
    if prompts_path is not None:
        print(f"[campaign] Prompts : {prompts_path}", flush=True)
        print(
            "[campaign] evo-x5 démarre llama-server sur l'hôte et ne recrée pas le conteneur llm.",
            flush=True,
        )
    print("[campaign] Plan seulement. Ajoute --run pour appliquer les profils et interroger l'agent.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vram", type=int, default=None, help="memory.total in MiB, if nvidia-smi is not used")
    parser.add_argument("--profiles", default="", help="comma-separated override, e.g. 24g,24g-30b")
    parser.add_argument(
        "--run",
        action="store_true",
        help="apply profiles, bring the chat server up, send the ping and the prompts",
    )
    parser.add_argument(
        "--prompts",
        default="",
        help="JSON list of {id, prompt}. Default for evo-x5: scripts/host/evo_campaign_prompts.json",
    )
    parser.add_argument("--workspace", default="", help="registered host path of the checkout (required with --run)")
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

    prompts_path: Path | None = None
    scenarios = SCENARIOS
    if args.prompts.strip() or profiles == ["evo-x5"]:
        prompts_path = Path(args.prompts) if args.prompts.strip() else EVO_PROMPTS
        scenarios = load_prompts(prompts_path)

    if not args.run:
        _print_plan(profiles, total, prompts_path)
        return 0

    if not profiles:
        print("[campaign] FATAL: aucun profil. Donne --vram ou --profiles.", file=sys.stderr)
        return 1
    if not args.workspace.strip():
        print(
            "[campaign] FATAL: --workspace est le chemin hôte d'une copie déjà enregistrée "
            "(python scripts/deploy.py workspace <chemin>).",
            file=sys.stderr,
        )
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
                        scenarios=scenarios,
                    )
                )
            except Exception as exc:
                rows.append({"profile": name, "status": "éliminé", "reason": str(exc), "scenarios": {}})
    finally:
        ENV_FILE.write_text(ENV_BACKUP.read_text(encoding="utf-8"), encoding="utf-8")
        if profiles and _profile_is_host(profiles[-1]):
            print(
                "[campaign] .env restauré depuis la copie. llama-server hôte est encore lancé.\n"
                "Pour le relancer : python scripts/deploy.py gpu-profile evo-x5, puis python scripts/deploy.py up.",
                flush=True,
            )
        else:
            print(
                "[campaign] .env restauré depuis la copie. Le conteneur llm est encore sur le dernier profil lancé.\n"
                "Pour le garder : python scripts/deploy.py gpu-profile <nom>, puis recrée llm.",
                flush=True,
            )

    report: dict[str, Any] = {"profiles": rows}
    if prompts_path is not None:
        report["prompts"] = str(prompts_path)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[campaign] rapport : {REPORT_PATH}", flush=True)
    for row in rows:
        scores = " ".join(f"{key}={value.get('status')}" for key, value in row.get("scenarios", {}).items())
        print(f"  {row['profile']}: {row.get('status')} {row.get('reason', '')} {scores}".rstrip(), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
