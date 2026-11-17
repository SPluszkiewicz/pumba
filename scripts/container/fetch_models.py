#!/usr/bin/env python3
"""Download GGUF model weights and verify their checksums.

This script is only ever meant to run INSIDE a container (the `setup`
compose profile's `fetch-models` service, built from
containers/devtools/Containerfile) - never assumed to run on the host
directly. See CONTRACTS.md for the pinned model repos/filenames this
mirrors.

Usage (from inside the container, working dir /workspace):
    python scripts/container/fetch_models.py            # chat + embedding + reranker
    python scripts/container/fetch_models.py --test     # tiny CI/smoke-test model only
    python scripts/container/fetch_models.py --hf-train-base
    python scripts/container/fetch_models.py --extra coder-30b-q5
    python scripts/container/fetch_models.py --force    # re-download even if present
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# The download itself is `huggingface_hub.hf_hub_download` (resume of an
# interrupted 5 GB transfer, retries, progress bar, ETag check against the
# hub). What stays ours is the *policy*: a SHA256 pinned in this file must
# match, or the file is deleted - the hub only guarantees the file is what
# the repo currently serves, not that it is what we audited.
from huggingface_hub import hf_hub_download

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/workspace/models"))
CHUNK_SIZE = 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo_id: str  # Hugging Face repo, e.g. "bartowski/Qwen2.5-7B-Instruct-GGUF"
    filename: str  # file inside the repo; also the local name under MODELS_DIR
    # SHA256 of the exact file, pinned against the model repo's published
    # checksum (see each ModelSpec below for provenance). `None` is still a
    # supported value for specs added later without a pin yet - it degrades
    # to "download but warn loudly instead of verifying" (see fetch()),
    # rather than silently trusting an unverified file.
    sha256: str | None
    # Further files from the same repo, each with its own SHA256. Split GGUFs
    # point `-m` at `filename` and keep the other shards beside it.
    extra_files: tuple[tuple[str, str], ...] = ()

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo_id}/resolve/main/{self.filename}"


# Per CONTRACTS.md. Hugging Face repos + exact quantized filenames.
# Chat model: Qwen2.5-Coder-14B-Instruct (code review). QLoRA stays on the 7B base.
CHAT_MODEL = ModelSpec(
    name="chat",
    repo_id="bartowski/Qwen2.5-Coder-14B-Instruct-GGUF",
    filename="Qwen2.5-Coder-14B-Instruct-Q5_K_M.gguf",
    # LFS oid (the SHA256) from
    # https://huggingface.co/api/models/bartowski/Qwen2.5-Coder-14B-Instruct-GGUF/tree/main
    # checked 2026-09-24. ~9.8 GiB.
    sha256="68701e9ffaab69e42827524f604bf71b375b7ed8f0c59f6a8c3d646c963b3e2c",
)

EMBED_MODEL = ModelSpec(
    name="embedding",
    repo_id="nomic-ai/nomic-embed-text-v1.5-GGUF",
    filename="nomic-embed-text-v1.5.Q8_0.gguf",
    # Verified the same way (see CHAT_MODEL's comment) - checked 2026-08-23.
    sha256="3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7",
)

RERANK_MODEL = ModelSpec(
    name="reranker",
    repo_id="gpustack/bge-reranker-v2-m3-GGUF",
    filename="bge-reranker-v2-m3-Q8_0.gguf",
    # LFS oid from the Hugging Face tree API (the oid is the SHA256).
    # https://huggingface.co/api/models/gpustack/bge-reranker-v2-m3-GGUF/tree/main
    # checked 2026-09-22. ~606 MiB.
    sha256="a43c7c9b11a4c1517e5bf95151960e1621d1b72f7a493364b01e386cf1aaa1d3",
)

# Optional chat models for the GPU cases in config/profiles/. `deploy.py up`
# does not download these. `fetch_models.py --extra <name>` fetches one.
EXTRA_MODELS: dict[str, ModelSpec] = {
    "coder-30b-q5": ModelSpec(
        name="coder-30b-q5",
        repo_id="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        filename="Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf",
        # LFS oid from the Hugging Face tree API, checked 2026-10-13. ~20.2 GiB.
        sha256="4b78837bbec5ee248e4a5642bf608b6793721af41b92589e40c8da0bce58b907",
    ),
    "coder-30b-q8": ModelSpec(
        name="coder-30b-q8",
        repo_id="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        filename="Qwen3-Coder-30B-A3B-Instruct-Q8_0.gguf",
        # LFS oid, same tree, checked 2026-10-13. ~30.3 GiB.
        sha256="4ff1cff607804037bf6d2168249c570baa4e1621292b159c0e06591e0d7c3066",
    ),
    "coder-32b-q5": ModelSpec(
        name="coder-32b-q5",
        repo_id="bartowski/Qwen2.5-Coder-32B-Instruct-GGUF",
        filename="Qwen2.5-Coder-32B-Instruct-Q5_K_M.gguf",
        # LFS oid, checked 2026-10-13. ~21.7 GiB.
        sha256="a4e15b1099adf59cade80da369400199e4d7922d7e16b717bbfcda4d08a0702f",
    ),
    # gpt-oss-20b, MXFP4, ~11.3 GiB. Official ggml-org conversion. Fits a 16 GB card.
    # LFS oid from the Hugging Face tree API, checked 2026-11-12.
    # https://huggingface.co/api/models/ggml-org/gpt-oss-20b-GGUF/tree/main
    "gpt-oss-20b": ModelSpec(
        name="gpt-oss-20b",
        repo_id="ggml-org/gpt-oss-20b-GGUF",
        filename="gpt-oss-20b-MXFP4.gguf",
        sha256="27cd6c432c7672cb812a92f611cf3ba7bbc35928262bb1e1253ff4ee6ae35901",
    ),
    # Mistral Small 4, 119B, 6.5B active. UD-Q5_K_M is three shards, ~83 GiB.
    # LFS oids from the Hugging Face tree API, checked 2026-11-03.
    # https://huggingface.co/api/models/unsloth/Mistral-Small-4-119B-2603-GGUF/tree/main/UD-Q5_K_M
    "mistral-small-4": ModelSpec(
        name="mistral-small-4",
        repo_id="unsloth/Mistral-Small-4-119B-2603-GGUF",
        filename="UD-Q5_K_M/Mistral-Small-4-119B-2603-UD-Q5_K_M-00001-of-00003.gguf",
        sha256="6d3a3d2428d254314a3305628afb4d27141be3597903fa5d61413b69b40e8e30",
        extra_files=(
            (
                "UD-Q5_K_M/Mistral-Small-4-119B-2603-UD-Q5_K_M-00002-of-00003.gguf",
                "f1340e0d2e7aa979028edffbc344bd3c8e3d4b1f0f8dd267bd4094b676f44142",
            ),
            (
                "UD-Q5_K_M/Mistral-Small-4-119B-2603-UD-Q5_K_M-00003-of-00003.gguf",
                "b6181cde0c0dd1e5823ec8abd51e705f98bec50e7be1e01eedb1239e1c9ac434",
            ),
        ),
    ),
}

TEST_MODEL = ModelSpec(
    name="test",
    repo_id="bartowski/Qwen2.5-0.5B-Instruct-GGUF",
    filename="Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
    # Verified the same way (see CHAT_MODEL's comment) - checked 2026-08-23.
    sha256="6eb923e7d26e9cea28811e1a8e852009b21242fb157b26149d3b188f3a8c8653",
)

# Hugging Face base for QLoRA. Same weights the evo-x5 GGUF was quantized from,
# so a converted adapter can load on mistral-small-4. The repo is ~242 GiB.
HF_TRAIN_BASE_REPO = "mistralai/Mistral-Small-4-119B-2603"
HF_TRAIN_BASE_REL = Path("hf") / "Mistral-Small-4-119B-2603"


class ChecksumMismatchError(RuntimeError):
    """Raised when a downloaded file's SHA256 doesn't match the pinned value.

    The caller (main()) treats this as fatal: the partially-verified file is
    deleted and the script exits non-zero. There is no silent fallback to
    "use it anyway" - a checksum mismatch means the file is either corrupted
    or not what we think it is, and continuing would defeat the entire point
    of pinning it.
    """


def _sha256_of(path: Path) -> str:
    """Hash `path`. A multi-gigabyte GGUF prints progress so the wait is visible."""
    total = path.stat().st_size
    digest = hashlib.sha256()
    done = 0
    step = 1024 * 1024 * 1024
    report_at = step
    print(
        f"[fetch_models] verifying {path.name} ({total / (1024 ** 3):.1f} GiB)",
        flush=True,
    )
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
            done += len(chunk)
            if done >= report_at or done == total:
                pct = (100 * done / total) if total else 100.0
                print(
                    f"[fetch_models]   {path.name}: {done / (1024 ** 3):.1f} / "
                    f"{total / (1024 ** 3):.1f} GiB ({pct:.0f}%)",
                    flush=True,
                )
                report_at = done + step
    return digest.hexdigest()


def _hub_local_dir(destination: Path, filename: str) -> str:
    """Directory the hub should use so a shard path is not nested twice.

    A flat file lives in ``MODELS_DIR``. ``UD-Q5_K_M/model-00001-of-00003.gguf``
    must use that same root: passing the shard directory makes the hub write
    ``UD-Q5_K_M/UD-Q5_K_M/...``.
    """
    parts = PurePosixPath(filename).parts
    return str(destination.parents[len(parts) - 1])


def _download(spec: ModelSpec, destination: Path, *, force: bool = False) -> None:
    """Fetch `spec` into `destination` (= MODELS_DIR / spec.filename) via the hub.

    `local_dir` makes the hub write the real file at `destination` (no
    symlink into a cache); an interrupted download resumes from the
    `.cache/huggingface/` bookkeeping the hub keeps next to it.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch_models] downloading {spec.url} -> {destination}", flush=True)
    downloaded = Path(
        hf_hub_download(
            repo_id=spec.repo_id,
            filename=spec.filename,
            local_dir=_hub_local_dir(destination, spec.filename),
            force_download=force,
        )
    )
    if downloaded.resolve() != destination.resolve():
        # Defensive: the hub returns where it wrote; with local_dir that is
        # destination itself, but never trust it silently.
        downloaded.replace(destination)


def fetch(spec: ModelSpec, *, force: bool) -> None:
    """Download `spec` and, when it is a split GGUF, each extra shard."""
    parts = [spec]
    for filename, sha in spec.extra_files:
        parts.append(ModelSpec(name=spec.name, repo_id=spec.repo_id, filename=filename, sha256=sha))
    for part in parts:
        _fetch_part(part, force=force)


def _fetch_part(spec: ModelSpec, *, force: bool) -> None:
    destination = MODELS_DIR / spec.filename

    if destination.exists() and not force:
        print(
            f"[fetch_models] {spec.filename} already on disk, download skipped.",
            flush=True,
        )
        return

    _download(spec, destination, force=force)

    if spec.sha256:
        actual = _sha256_of(destination)
        if actual != spec.sha256:
            destination.unlink(missing_ok=True)
            raise ChecksumMismatchError(
                f"{spec.filename}: downloaded file's SHA256 ({actual}) does not match "
                f"the pinned value ({spec.sha256}). Deleted the corrupt/unexpected download."
            )
        print(f"[fetch_models] {spec.filename}: checksum verified")
    else:
        print(
            f"[fetch_models] WARNING: no pinned SHA256 for {spec.filename} yet - "
            "downloaded but NOT integrity-verified. Fill in ModelSpec.sha256 in this "
            "script once you have a trusted checksum for this file.",
            file=sys.stderr,
        )


def fetch_hf_train_base(*, force: bool) -> None:
    """Download the Mistral Small 4 safetensors for the trainer profile."""
    destination = MODELS_DIR / HF_TRAIN_BASE_REL
    marker = destination / "config.json"
    if marker.exists() and not force:
        print(
            f"[fetch_models] HF train base already present at {destination}, "
            "skipping (use --force to re-download)"
        )
        return

    from huggingface_hub import snapshot_download

    if destination.exists() and force:
        import shutil

        shutil.rmtree(destination)

    print(f"[fetch_models] downloading {HF_TRAIN_BASE_REPO} -> {destination}", flush=True)
    snapshot_download(
        repo_id=HF_TRAIN_BASE_REPO,
        local_dir=str(destination),
    )
    if not marker.exists():
        raise RuntimeError(f"HF download finished but {marker} is missing")
    print(f"[fetch_models] HF train base ready at {destination}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test", action="store_true", help="fetch only the tiny CI/smoke-test model instead of the full-size models"
    )
    parser.add_argument(
        "--hf-train-base",
        action="store_true",
        help="fetch Hugging Face Mistral Small 4 weights for QLoRA training (~242 GiB)",
    )
    parser.add_argument(
        "--extra",
        choices=sorted(EXTRA_MODELS),
        help="fetch one optional chat GGUF (not part of the default `up` download)",
    )
    parser.add_argument("--force", action="store_true", help="re-download even if the file already exists")
    args = parser.parse_args()

    if args.extra:
        try:
            fetch(EXTRA_MODELS[args.extra], force=args.force)
        except ChecksumMismatchError as exc:
            print(f"[fetch_models] FATAL: {exc}", file=sys.stderr)
            return 1
        print("[fetch_models] done.")
        return 0

    if args.hf_train_base:
        try:
            fetch_hf_train_base(force=args.force)
        except RuntimeError as exc:
            print(f"[fetch_models] FATAL: {exc}", file=sys.stderr)
            return 1
        print("[fetch_models] done.")
        return 0

    # --test swaps in the tiny chat model but still needs the real embedder
    # model (embedder is never swapped - see CONTRACTS.md: changing it would
    # change the embedding vector dimension and break the 768-dim Qdrant
    # collection assumption everywhere).
    # --test skips the reranker (~606 MiB). Smoke and eval_rag keep RRF order
    # because .env.test leaves RERANKER_BASE_URL empty.
    specs = [TEST_MODEL, EMBED_MODEL] if args.test else [CHAT_MODEL, EMBED_MODEL, RERANK_MODEL]

    try:
        for spec in specs:
            fetch(spec, force=args.force)
    except ChecksumMismatchError as exc:
        print(f"[fetch_models] FATAL: {exc}", file=sys.stderr)
        return 1

    print("[fetch_models] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
