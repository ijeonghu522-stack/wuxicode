"""train_qlora.py 를 최신 TRL(SFTConfig) 스타일로 업그레이드한다.

이전 실패 원인:
  - transformers==4.45 / trl==0.9 는 TrainingArguments + tokenizer= 만 지원
  - 최신 TRL 은 SFTConfig + processing_class= 필요
  - T4(cc 7.5) 에서 bf16=True 하드 에러

사용법:
    python training/fix_train_qlora.py
    python training/fix_train_qlora.py --path other/train_qlora.py

주의: 이미 패치된 파일이면 아무것도 하지 않는다 (idempotent).
"""
import argparse
import re
import sys
from pathlib import Path

NEW_TRAINER_BLOCK = '''    # gradient checkpointing 시 입력 gradient 필요 (QLoRA 필수)
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()

    # T4(cc 7.5) 는 bf16 미지원 -> 자동 감지
    use_bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()

    from trl import SFTConfig

    sft_cfg = SFTConfig(
        output_dir=t["output_dir"],
        per_device_train_batch_size=t["per_device_train_batch_size"],
        gradient_accumulation_steps=t["gradient_accumulation_steps"],
        num_train_epochs=t["num_train_epochs"],
        learning_rate=t["learning_rate"],
        max_grad_norm=t.get("max_grad_norm", 0.3),
        lr_scheduler_type=t.get("lr_scheduler_type", "cosine"),
        warmup_steps=t.get("warmup_steps", 10),
        logging_steps=1,
        save_steps=t.get("save_steps", 50),
        save_total_limit=t.get("save_total_limit", 2),
        bf16=use_bf16,
        fp16=not use_bf16,
        gradient_checkpointing=True,
        optim=t.get("optim", "paged_adamw_8bit"),
        report_to="none",
        remove_unused_columns=False,
        max_length=t.get("max_seq_length", 4096),
        dataset_text_field="text",
        packing=False,
    )
    trainer = SFTTrainer(
        model=model,
        processing_class=tok,
        train_dataset=ds,
        args=sft_cfg,
    )
'''

RE_TA = re.compile(r"[ \t]*ta = TrainingArguments\([\s\S]*?\n[ \t]*\)\n")
RE_SFT = re.compile(r"[ \t]*trainer = SFTTrainer\([\s\S]*?\n[ \t]*\)\n")
RE_WARMUP_RATIO = re.compile(r"[ \t]*warmup_ratio=t\.get\([^\n]*\n")
RE_SFT_IMPORT = re.compile(r"from trl import ([^\n]*)\n")


def already_patched(src):
    return "SFTConfig" in src and "processing_class" in src


def patch(src):
    if already_patched(src):
        return src, []

    changes = []
    had_ta = bool(RE_TA.search(src))
    had_sft = bool(RE_SFT.search(src))
    if not had_ta and not had_sft:
        sys.exit(
            "[FAIL] TrainingArguments / SFTTrainer 블록을 찾지 못했습니다.\n"
            "       train_qlora.py 가 예상과 다른 버전인지 확인하세요."
        )

    src = RE_WARMUP_RATIO.sub("", src)
    src = RE_SFT_IMPORT.sub(lambda m: f"from trl import {m.group(1).strip()}, SFTConfig\n", src)

    if had_ta:
        src = RE_TA.sub("", src)
        changes.append("TrainingArguments 제거")

    if had_sft:
        src = RE_SFT.sub(NEW_TRAINER_BLOCK, src)
        changes.append("SFTTrainer -> SFTConfig")

    src = src.replace("tokenizer=tok,", "processing_class=tok,")
    changes.append("tokenizer -> processing_class")

    return src, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="training/train_qlora.py")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        sys.exit(f"[FAIL] 파일 없음: {path}")

    # utf-8-sig: BOM 이 붙어 있으면 compile() 이 U+FEFF 에 걸린다.
    # 읽을 때 BOM 을 벗겨고, 저장할 때는 BOM 없이(순수 UTF-8) 쓴다.
    src = path.read_text(encoding="utf-8-sig")

    if already_patched(src):
        print(f"[SKIP] 이미 패치됨: {path}")
        return

    patched, changes = patch(src)

    compile(patched, str(path), "exec")

    path.write_text(patched, encoding="utf-8")
    print(f"[OK] {path}")
    for c in changes:
        print(f"  - {c}")
    print("[OK] 문법 검증 통과")


if __name__ == "__main__":
    main()