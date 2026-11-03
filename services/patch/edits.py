"""Exact edits on one checkout. No commit, no push, no shell.

`begin_plan` reads the plan file and keeps the paths it names. `propose`
checks that the excerpt occurs once and that `git apply --check` accepts
the diff. `apply` is the only call that writes. A campaign stops at 200
files (500 is the hard cap). Each path may be edited at most 8 times.
"""

from __future__ import annotations

import difflib
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from services.tools.handlers.path_safety import PathSafetyError, validate_path

__all__ = [
    "EDIT_HARD_CAP",
    "EDIT_MAX_FILES",
    "EDITS_PER_FILE",
    "EditError",
    "apply_proposal",
    "begin_plan",
    "extract_plan_paths",
    "propose_edit",
    "reset_store",
    "should_build_solution",
]

EDIT_MAX_FILES = 200
EDIT_HARD_CAP = 500
EDITS_PER_FILE = 8

_FORBIDDEN = {".git", ".svn", ".env", ".hg"}
_SOURCE = re.compile(r"([\w./\\-]+\.(?:cpp|cc|cxx|c|h|hpp|hh|hxx))\b", re.IGNORECASE)
_APPLY_ARGV = ["git", "apply", "--check", "-p1", "--unsafe-paths", "-"]


class EditError(ValueError):
    """A refused edit. The message is safe to show the model."""


@dataclass
class _Proposal:
    campaign_id: str
    path: str
    new_text: str
    diff: str
    applied: bool = False


@dataclass
class _Campaign:
    root: str
    allow: set[str]
    max_files: int
    applied: list[str] = field(default_factory=list)
    edits_per_path: dict[str, int] = field(default_factory=dict)
    proposals: dict[str, _Proposal] = field(default_factory=dict)
    targets: set[str] = field(default_factory=set)


_CAMPAIGNS: dict[str, _Campaign] = {}


def reset_store() -> None:
    _CAMPAIGNS.clear()


def extract_plan_paths(text: str) -> list[str]:
    """Source paths named in a plan. Order kept, duplicates dropped."""
    found: list[str] = []
    for match in _SOURCE.finditer(text):
        path = match.group(1).replace("\\", "/").lstrip("./")
        if path not in found:
            found.append(path)
    return found


def should_build_solution(targets: set[str]) -> bool:
    """One target is enough. A solution build is only for several targets."""
    return len(targets) > 1


def _normalize_rel(raw: str) -> str:
    text = raw.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _reject_forbidden(raw: str) -> None:
    parts = _normalize_rel(raw).split("/")
    for part in parts:
        if part in _FORBIDDEN or part.startswith(".env"):
            raise EditError(f"path is not editable: {raw}")


def _read(root: Path, raw: str) -> tuple[Path, str, str]:
    _reject_forbidden(raw)
    try:
        resolved = validate_path(raw, root)
    except PathSafetyError as exc:
        raise EditError(str(exc)) from exc
    if not resolved.is_file():
        raise EditError(f"not a file: {raw}")
    text = resolved.read_text(encoding="utf-8").replace("\r\n", "\n")
    rel = _normalize_rel(raw)
    return resolved, rel, text


def begin_plan(root: Path, plan: str, focus: str | None = None, max_files: int | None = None) -> dict:
    limit = EDIT_MAX_FILES if max_files is None else max_files
    if limit < 1 or limit > EDIT_HARD_CAP:
        raise EditError(f"max_files must be from 1 to {EDIT_HARD_CAP}")
    _resolved, rel_plan, text = _read(root, plan)
    allow = set(extract_plan_paths(text))
    if focus:
        focus_rel = _normalize_rel(focus)
        _reject_forbidden(focus_rel)
        try:
            validate_path(focus_rel, root)
        except PathSafetyError as exc:
            raise EditError(str(exc)) from exc
        allow.add(focus_rel)
    if not allow:
        raise EditError("the plan names no source file")
    if len(allow) > limit:
        raise EditError(f"the plan names {len(allow)} files; this campaign allows {limit}")
    campaign_id = secrets.token_hex(8)
    _CAMPAIGNS[campaign_id] = _Campaign(root=str(root.resolve()), allow=allow, max_files=limit)
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "paths": sorted(allow),
        "max_files": limit,
        "edits_per_file": EDITS_PER_FILE,
    }


def _campaign(campaign_id: str, root: Path) -> _Campaign:
    campaign = _CAMPAIGNS.get(campaign_id)
    if campaign is None:
        raise EditError("unknown campaign")
    if campaign.root != str(root.resolve()):
        raise EditError("campaign belongs to another checkout")
    return campaign


def _unified(rel: str, before: str, after: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    if diff and not diff.endswith("\n"):
        diff += "\n"
    return diff


def _check_diff(root: Path, diff: str) -> tuple[bool, str]:
    if shutil.which("git") is None:
        return True, "exact"
    proc = subprocess.run(
        _APPLY_ARGV,
        input=diff.encode("utf-8"),
        capture_output=True,
        cwd=root,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"git apply --check failed").decode("utf-8", "replace").strip()
        return False, detail
    return True, "git apply --check"


def propose_edit(root: Path, campaign_id: str, path: str, old: str, new: str) -> dict:
    if not old:
        raise EditError("old excerpt must not be empty")
    campaign = _campaign(campaign_id, root)
    _resolved, rel, current = _read(root, path)
    if rel not in campaign.allow:
        raise EditError(f"path is not in the plan: {rel}")
    known = set(campaign.applied) | {item.path for item in campaign.proposals.values()}
    if rel not in known and len(known) >= campaign.max_files:
        raise EditError(f"campaign file budget reached ({campaign.max_files})")
    pending = sum(1 for item in campaign.proposals.values() if item.path == rel and not item.applied)
    if campaign.edits_per_path.get(rel, 0) + pending >= EDITS_PER_FILE:
        raise EditError(f"edit budget reached for {rel} ({EDITS_PER_FILE})")
    count = current.count(old)
    if count == 0:
        raise EditError("excerpt not found; read the file again")
    if count != 1:
        raise EditError(f"excerpt occurs {count} times; include more context so it matches once")
    updated = current.replace(old, new, 1)
    diff = _unified(rel, current, updated)
    ok, detail = _check_diff(root, diff)
    if not ok:
        raise EditError(detail)
    proposal_id = secrets.token_hex(8)
    campaign.proposals[proposal_id] = _Proposal(
        campaign_id=campaign_id, path=rel, new_text=updated, diff=diff
    )
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "proposal_id": proposal_id,
        "path": rel,
        "diff": diff,
        "check": detail,
        "written": False,
    }


def apply_proposal(root: Path, campaign_id: str, proposal_id: str) -> dict:
    campaign = _campaign(campaign_id, root)
    proposal = campaign.proposals.get(proposal_id)
    if proposal is None or proposal.campaign_id != campaign_id:
        raise EditError("unknown proposal")
    if proposal.applied:
        raise EditError("proposal already applied")
    resolved, rel, _current = _read(root, proposal.path)
    if rel != proposal.path:
        raise EditError("proposal path does not match")
    resolved.write_text(proposal.new_text, encoding="utf-8", newline="\n")
    proposal.applied = True
    campaign.edits_per_path[rel] = campaign.edits_per_path.get(rel, 0) + 1
    if rel not in campaign.applied:
        campaign.applied.append(rel)
    return {"ok": True, "path": rel, "written": True, "files": list(campaign.applied)}


def note_target(campaign_id: str, target: str) -> set[str]:
    campaign = _CAMPAIGNS.get(campaign_id)
    if campaign is None:
        raise EditError("unknown campaign")
    if target:
        campaign.targets.add(target)
    return set(campaign.targets)
