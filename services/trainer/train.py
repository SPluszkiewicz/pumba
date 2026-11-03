#!/usr/bin/env python3
"""QLoRA fine-tune Qwen2.5-7B-Instruct on a JSONL chat dataset.

Runs inside the `trainer` compose profile (GPU). Reads the latest dataset
under TRAIN_DATASET_DIR or models/training/{commit}/dataset.jsonl, writes
PEFT adapter to TRAIN_ADAPTER_OUT/{commit_sha}/ and refreshes latest/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dataset import resolve_commit_sha

# services/ on sys.path (same trick dataset.py uses for the indexer's chunker)
# so the shared structured logger is importable in the container and in tests.
_SERVICES_DIR = Path(__file__).resolve().parent.parent
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))

from common.logging import get_logger  # noqa: E402

log = get_logger("trainer")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    return int(raw) if raw.strip().isdigit() else default


def _find_dataset(dataset_dir: Path, commit_sha: str) -> Path:
    candidate = dataset_dir / commit_sha / "dataset.jsonl"
    if candidate.exists():
        return candidate
    # Newest run dir with a dataset.
    runs = sorted(
        (p for p in dataset_dir.iterdir() if p.is_dir() and (p / "dataset.jsonl").exists()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not runs:
        raise FileNotFoundError(f"no dataset.jsonl under {dataset_dir}")
    return runs[0] / "dataset.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _format_example(messages: list[dict[str, str]], tokenizer) -> str:
    """Apply the model chat template to a messages list."""
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)


def _convert_lora_to_gguf(
    *,
    base_model: Path,
    adapter_dir: Path,
    outfile: Path,
    llama_cpp_dir: Path,
) -> None:
    script = llama_cpp_dir / "convert_lora_to_gguf.py"
    if not script.exists():
        raise FileNotFoundError(f"convert_lora_to_gguf.py not found at {script}")
    outfile.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script),
        str(adapter_dir),
        "--outfile",
        str(outfile),
        "--outtype",
        "f16",
        "--base",
        str(base_model),
    ]
    print(f"[train] $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def main() -> int:
    base_model = Path(os.environ.get("TRAIN_BASE_MODEL", "/models/hf/Qwen2.5-7B-Instruct"))
    adapter_out = Path(os.environ.get("TRAIN_ADAPTER_OUT", "/models/adapters"))
    dataset_dir = Path(os.environ.get("TRAIN_DATASET_DIR", "/models/training"))
    source_root = Path(os.environ.get("TRAIN_SOURCE_ROOT", "/workspace/app"))
    llama_cpp_dir = Path(os.environ.get("LLAMA_CPP_DIR", "/opt/llama.cpp"))

    commit_sha = resolve_commit_sha(source_root)
    dataset_path = _find_dataset(dataset_dir, commit_sha)
    rows = _load_jsonl(dataset_path)
    if not rows:
        print("[train] FATAL: dataset is empty", file=sys.stderr)
        return 1

    if not base_model.is_dir():
        print(f"[train] FATAL: TRAIN_BASE_MODEL not found: {base_model}", file=sys.stderr)
        return 1

    # Lazy imports - heavy deps only in trainer container.
    import torch
    from datasets import Dataset
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    lora_rank = _env_int("TRAIN_LORA_RANK", 16)
    lora_alpha = _env_int("TRAIN_LORA_ALPHA", 32)
    max_steps = _env_int("TRAIN_MAX_STEPS", 200)
    max_seq_length = _env_int("TRAIN_MAX_SEQ_LENGTH", 2048)

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    texts = [_format_example(row["messages"], tokenizer) for row in rows]
    train_dataset = Dataset.from_dict({"text": texts})

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    latest_adapter = adapter_out / "latest"
    resume_adapter = latest_adapter if (latest_adapter / "adapter_config.json").exists() else None

    if resume_adapter is not None:
        print(f"[train] continuing from adapter {resume_adapter}", flush=True)
        model = AutoModelForCausalLM.from_pretrained(
            base_model,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )
        model = PeftModel.from_pretrained(model, str(resume_adapter), is_trainable=True)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            base_model,
            quantization_config=bnb_config,
            device_map="auto",
            trust_remote_code=True,
        )
        model = prepare_model_for_kbit_training(model)
        lora_config = LoraConfig(
            r=lora_rank,
            lora_alpha=lora_alpha,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        )
        model = get_peft_model(model, lora_config)

    run_dir = adapter_out / commit_sha
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    training_args = SFTConfig(
        output_dir=str(run_dir),
        max_steps=max_steps,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=2e-4,
        logging_steps=10,
        save_strategy="no",
        bf16=True,
        max_seq_length=max_seq_length,
        dataset_text_field="text",
        packing=False,
        report_to=[],
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        processing_class=tokenizer,
    )
    trainer.train()

    model.save_pretrained(run_dir)
    tokenizer.save_pretrained(run_dir)

    gguf_path = run_dir / "adapter.gguf"
    try:
        _convert_lora_to_gguf(
            base_model=base_model,
            adapter_dir=run_dir,
            outfile=gguf_path,
            llama_cpp_dir=llama_cpp_dir,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"[train] WARNING: LoRA GGUF conversion failed: {exc}", file=sys.stderr)

    # Refresh latest/ symlink target (copy tree for portability on Windows bind mounts).
    if latest_adapter.exists():
        shutil.rmtree(latest_adapter)
    shutil.copytree(run_dir, latest_adapter)

    manifest = {
        "commit_sha": commit_sha,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "num_examples": len(rows),
        "dataset_path": str(dataset_path),
        "adapter_dir": str(run_dir),
        "gguf_path": str(gguf_path) if gguf_path.exists() else None,
        "base_model": str(base_model),
        "max_steps": max_steps,
        "lora_rank": lora_rank,
    }
    (latest_adapter / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    log.info("train_complete", **manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
