import re

path = "training/train_qlora.py"
s = open(path, encoding="utf-8").read()

# 1. warmup_ratio 라인 제거
s = s.replace('        warmup_ratio=t.get("warmup_ratio", 0.03),\n', '')

# 2. tokenizer=tok -> processing_class=tok
s = s.replace("tokenizer=tok,", "processing_class=tok,")

# 3. TrainingArguments 블록 전체 제거
old_ta = r'    ta = TrainingArguments\([\s\S]*?\)\n'
s = re.sub(old_ta, '', s)

# 4. SFTTrainer 블록 전체 교체
old_sft = r'    trainer = SFTTrainer\([\s\S]*?\)\n'
new_sft = '''    from trl import SFTConfig

    sft_cfg = SFTConfig(
        output_dir=t["output_dir"],
        per_device_train_batch_size=t["per_device_train_batch_size"],
        gradient_accumulation_steps=t["gradient_accumulation_steps"],
        num_train_epochs=t["num_train_epochs"],
        learning_rate=t["learning_rate"],
        warmup_steps=10,
        logging_steps=1,
        save_steps=50,
        bf16=True,
        gradient_checkpointing=True,
        report_to="none",
        max_length=t.get("max_seq_length", 32768),
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
s = re.sub(old_sft, new_sft, s)

open(path, "w", encoding="utf-8").write(s)
print("✓ 패치 완료")