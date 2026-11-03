#!/usr/bin/env python3
"""Retrieval baseline for one registered checkout.

Calls POST /lookup with include_candidates. It does not call the chat model.
A low hit rate is the measurement, not a failed process: the exit code is
non-zero only when the checkout is missing, the tree is dirty under
--require-clean, or the agent does not answer.

    python scripts/deploy.py eval D:/rest/opencv --questions scripts/host/eval_opencv_questions.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import workspaces

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENT = "http://127.0.0.1:8090"


@dataclass(frozen=True)
class Question:
    id: str
    family: str
    query: str
    expected_file_substring: str | None = None
    expected_symbol_substring: str | None = None
    expect_absent_symbol: str | None = None
    expected_file_exact: bool = False


def load_questions(path: Path) -> list[Question]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw["questions"] if isinstance(raw, dict) else raw
    questions: list[Question] = []
    for item in items:
        questions.append(
            Question(
                id=item["id"],
                family=item.get("family", "symbol"),
                query=item["query"],
                expected_file_substring=item.get("expected_file_substring"),
                expected_symbol_substring=item.get("expected_symbol_substring"),
                expect_absent_symbol=item.get("expect_absent_symbol"),
                expected_file_exact=bool(item.get("expected_file_exact")),
            )
        )
    return questions


def _match(
    rows: list[dict],
    file_sub: str | None,
    symbol_sub: str | None,
    *,
    exact: bool = False,
) -> bool:
    for row in rows:
        path = str(row.get("file_path") or "").replace("\\", "/")
        symbol = str(row.get("symbol_name") or "")
        if file_sub:
            if exact:
                if path != file_sub:
                    continue
            elif file_sub not in path:
                continue
        if symbol_sub and symbol_sub not in symbol:
            continue
        return True
    return False


def score(question: Question, retrieval: dict) -> dict:
    """Place the expected file in the prefetch, the reranked window, or the final 5."""
    candidates = list(retrieval.get("candidates") or [])
    reranked = list(retrieval.get("reranked") or [])
    index_hits = list(retrieval.get("index_hits") or [])
    if question.expect_absent_symbol:
        needle = question.expect_absent_symbol
        invented = any(needle in str(row.get("symbol_name") or "") for row in candidates + index_hits)
        return {
            "in_index_hits": False,
            "in_reranked": False,
            "in_candidates": False,
            "note": "invented symbol appeared" if invented else "invented symbol stayed absent",
            "negative": True,
            "negative_ok": not invented,
        }
    file_sub = question.expected_file_substring or ""
    symbol_sub = question.expected_symbol_substring
    exact = question.expected_file_exact
    in_candidates = _match(candidates, file_sub, symbol_sub, exact=exact)
    in_reranked = _match(reranked, file_sub, symbol_sub, exact=exact)
    in_index_hits = _match(index_hits, file_sub, symbol_sub, exact=exact)
    if in_index_hits:
        note = "in the 5"
    elif in_reranked:
        note = "dropped by the noise filter"
    elif in_candidates:
        note = "in the prefetch, not in the 5"
    else:
        note = "absent from the prefetch"
    return {
        "in_index_hits": in_index_hits,
        "in_reranked": in_reranked,
        "in_candidates": in_candidates,
        "note": note,
        "negative": False,
        "negative_ok": False,
    }


def read_token(env_file: Path) -> str:
    if not env_file.is_file():
        return os.environ.get("AGENT_BEARER_TOKEN", "")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "AGENT_BEARER_TOKEN":
            return value.strip().strip('"').strip("'")
    return os.environ.get("AGENT_BEARER_TOKEN", "")


def git_facts(path: Path) -> dict:
    def run(args: list[str]) -> str:
        try:
            result = subprocess.run(
                args,
                cwd=path,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    revision = run(["git", "rev-parse", "HEAD"])
    dirty = run(["git", "status", "--porcelain"])
    return {
        "revision": revision,
        "dirty_files": len([line for line in dirty.splitlines() if line.strip()]) if dirty else 0,
    }


def find_registration(path: Path) -> dict | None:
    resolved = path.resolve()
    want = {
        workspaces.normalize_host_path(item)
        for item in workspaces.path_spellings(resolved.as_posix())
    }
    data = workspaces.load_registry()
    for item in data.get("checkouts") or []:
        stored = {
            workspaces.normalize_host_path(spelling)
            for spelling in workspaces.path_spellings(str(item.get("path") or ""))
        }
        if not (stored & want):
            continue
        repo = (data.get("repos") or {}).get(item.get("repo_id") or "", {})
        return {
            "path": item.get("path"),
            "collection": repo.get("collection") or item.get("repo_id"),
            "vcs": repo.get("vcs"),
            "indexed": bool(repo.get("indexed")),
            "url": repo.get("url"),
        }
    return None


def lookup(agent: str, token: str, query: str, workspace: str, timeout: float) -> dict:
    body = json.dumps(
        {"query": query, "workspace": workspace, "include_candidates": True}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{agent.rstrip('/')}/lookup",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"agent unreachable: {exc.reason}") from exc


def render_report(header: dict, rows: list[dict]) -> str:
    measured = [row for row in rows if not row["negative"]]
    negatives = [row for row in rows if row["negative"]]
    hit5 = sum(1 for row in measured if row["in_index_hits"])
    in_prefetch = sum(1 for row in measured if row["in_candidates"] and not row["in_index_hits"])
    absent = sum(1 for row in measured if not row["in_candidates"])
    lines = [
        "Retrieval baseline. This is a measurement, not a release gate.",
        f"checkout: {header['checkout']}",
        f"revision: {header['revision'] or '(none)'}",
        f"dirty_files: {header['dirty_files']}",
        f"collection: {header['collection']}",
        f"indexed: {header['indexed']}",
        f"reranker_used: {header['reranker_used']}",
        f"hit@5: {hit5}/{len(measured)}",
        f"in the prefetch but not in the 5: {in_prefetch}",
        f"absent from the prefetch: {absent}",
    ]
    families: list[str] = []
    for row in measured:
        if row["family"] not in families:
            families.append(row["family"])
    if len(families) > 1:
        for family in families:
            group = [row for row in measured if row["family"] == family]
            hit = sum(1 for row in group if row["in_index_hits"])
            lines.append(f"{family}: {hit}/{len(group)} in the 5")
    lines.extend(
        [
            "",
            f"{'id':<18} {'family':<10} {'in5':<4} {'prefetch':<9} note",
        ]
    )
    for row in rows:
        if row["negative"]:
            flag = "ok" if row["negative_ok"] else "FAIL"
            lines.append(f"{row['id']:<18} {row['family']:<10} {'':<4} {'':<9} {flag} {row['note']}")
            continue
        lines.append(
            f"{row['id']:<18} {row['family']:<10} "
            f"{'yes' if row['in_index_hits'] else 'no':<4} "
            f"{'yes' if row['in_candidates'] else 'no':<9} {row['note']}"
        )
    if negatives:
        held = sum(1 for row in negatives if row["negative_ok"])
        lines.append(f"invented symbols kept out: {held}/{len(negatives)}")
    return "\n".join(lines)


def evaluate(
    path: Path,
    questions: list[Question],
    *,
    agent: str,
    token: str,
    timeout: float,
    require_indexed: bool = True,
) -> tuple[dict, list[dict]]:
    registration = find_registration(path)
    if registration is None:
        raise SystemExit(
            f"{path} is not a registered checkout. Run: python scripts/deploy.py workspace {path}"
        )
    if require_indexed and not registration["indexed"]:
        raise SystemExit(
            f"{path} is registered as {registration['collection']} but the index has not finished. "
            "Pass --allow-unfinished to score the collection anyway."
        )
    facts = git_facts(path)
    rows: list[dict] = []
    reranker_used = None
    for question in questions:
        payload = lookup(agent, token, question.query, str(path), timeout)
        retrieval = payload.get("retrieval") or {}
        if reranker_used is None:
            reranker_used = bool(retrieval.get("reranker_used"))
        row = score(question, retrieval)
        row.update({"id": question.id, "family": question.family, "query": question.query})
        rows.append(row)
        print(f"{row['id']}: {row['note']}", flush=True)
    header = {
        "checkout": str(path),
        "revision": facts["revision"],
        "dirty_files": facts["dirty_files"],
        "collection": registration["collection"],
        "indexed": registration["indexed"],
        "reranker_used": reranker_used,
    }
    return header, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="registered checkout, e.g. D:/rest/opencv")
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--agent", default=os.environ.get("AGENT_BASE_URL", DEFAULT_AGENT))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", type=Path, default=None, help="also write the report JSON here")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="exit non-zero when git status is not empty; dirty files displace the 5 hits",
    )
    parser.add_argument(
        "--allow-unfinished",
        action="store_true",
        help="score even when the registry does not mark the index finished",
    )
    args = parser.parse_args(argv)
    token = read_token(REPO_ROOT / ".env")
    if not token:
        print("AGENT_BEARER_TOKEN is missing from .env", file=sys.stderr)
        return 1
    checkout = args.path.expanduser().resolve()
    questions = load_questions(args.questions)
    try:
        header, rows = evaluate(
            checkout,
            questions,
            agent=args.agent,
            token=token,
            timeout=args.timeout,
            require_indexed=not args.allow_unfinished,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    text = render_report(header, rows)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"header": header, "rows": rows}, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.require_clean and header["dirty_files"]:
        print(
            f"checkout has {header['dirty_files']} uncommitted paths; "
            "working-copy hits can displace index hits",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
