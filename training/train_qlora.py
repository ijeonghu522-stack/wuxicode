"""WX-1 QLoRA 학습 스크립트 (Colab T4용)"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
)
from trl import SFTTrainer


def load_yaml(path: Path) -> dict:
    """의존성 없이 YAML 파싱 (간단)."""
    import yaml
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def format_chat(example: dict) -> dict:
    """messages → text 포맷."""
    msgs = example["messages"]
    text = ""
    for m in msgs:
        role = m["role"]
        content = m["content"]
        if role == "system":
            text += f"<|im_start|>system\n{content}<|im_end|>\n"
        elif role == "user":
            text += f"<|im_start|>user\n{content}<|im_end|>\n"
        elif role == "assistant":
            text += f"<|im_start|>assistant\n{content}<|im_end|>\n"
    return {"text": text}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    args = p.parse_args()

    cfg = load_yaml(Path(args.config))
    print("=" * 60)
    print(f"[WX-1] 베이스: {cfg['base_model']}")
    print(f"[WX-1] 데이터: {cfg['dataset']['train_file']}")
    print(f"[WX-1] LoRA r={cfg['lora']['r']}, max_seq={cfg['training']['max_seq_length']}")
    print("=" * 60)

    # 4bit 양자화
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    # 토크나이저
    tok = AutoTokenizer.from_pretrained(cfg["base_model"], trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # 모델
    print("[WX-1] 모델 로드 중...")
    model = AutoModelForCausalLM.from_pretrained(
        cfg["base_model"],
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
    )
    model = prepare_model_for_kbit_training(model)
    model.config.use_cache = False

    # LoRA
    lora = LoraConfig(
        r=cfg["lora"]["r"],
        lora_alpha=cfg["lora"]["alpha"],
        lora_dropout=cfg["lora"]["dropout"],
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    # 데이터셋
    print("[WX-1] 데이터셋 로드 중...")
    ds = load_dataset("json", data_files=cfg["dataset"]["train_file"])["train"]
    max_n = cfg["dataset"].get("max_samples", 0)
    if max_n and max_n > 0:
        ds = ds.select(range(min(max_n, len(ds))))
    ds = ds.map(format_chat, remove_columns=ds.column_names)
    print(f"[WX-1] 학습 샘플: {len(ds)}개")

    # 학습
    t = cfg["training"]
    ta = TrainingArguments(
        output_dir=t["output_dir"],
        per_device_train_batch_size=t["per_device_train_batch_size"],
        gradient_accumulation_steps=t["gradient_accumulation_steps"],
        num_train_epochs=t["num_train_epochs"],
        learning_rate=t["learning_rate"],
        max_grad_norm=0.3,
        warmup_ratio=t.get("warmup_ratio", 0.03),
        lr_scheduler_type=t.get("lr_scheduler_type", "cosine"),
        logging_steps=1,
        save_steps=t.get("save_steps", 50),
        save_total_limit=2,
        bf16=True,
        gradient_checkpointing=True,
        report_to="none",
        optim="paged_adamw_8bit",
        remove_unused_columns=False,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tok,
        train_dataset=ds,
        args=ta,
        dataset_text_field="text",
        max_seq_length=t["max_seq_length"],
        packing=False,
    )

    print("[WX-1] 학습 시작!")
    trainer.train()
    trainer.save_model(t["output_dir"])
    tok.save_pretrained(t["output_dir"])
    print(f"[WX-1] 완료: {t['output_dir']}")


if __name__ == "__main__":
    main()
