"""LoRA 어댑터를 베이스 모델에 병합한다.

사용법:
    python training/merge_adapter.py \
        --base Qwen/Qwen2.5-Coder-7B-Instruct \
        --adapter output/wx-1-coder-lora \
        --output output/wx-1-coder-merged \
        --dtype float16

메모리 주의 (병합은 CPU 에서 fp16 으로 되므로 VRAM 이 아니라 시스템 RAM 이 걸림):
    7B fp16 = 파라미터 7.6B x 2 byte = 약 15.2GB.
      - Colab 무료 T4 : RAM 12.7GB -> OOM (Killed). 1.5B 로 낮추거나 런타임 상향.
      - Kaggle  GPU   : RAM 29GB   -> 통과. 7B 그대로 가능.

주의:
    base_model 은 Qwen2.5-Coder-* 여야 한다. merge 후 tokenizer/config 가
    base_model 쪽에서 온다. LoRA 를 학습한 베이스와 다른 모델을 넘기면
    어댑터 shape 이 맞지 않아 병합은 되지만 결과가 nonsense 가 된다.

주의 (transformers):
    dtype= 인자는 transformers 4.56 (PR #39782) 부터 유효하다.
    그 이하에서는 torch_dtype= 를 써야 한다. 노트북 핀은 >=4.56 기준.
"""
import argparse
import sys
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

DTYPES = {"float16": torch.float16, "bfloat16": torch.bfloat16}


def estimate_ram_gb(dtype, params_b):
    return params_b * (2 if dtype in (torch.float16, torch.bfloat16) else 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--dtype", default="float16", choices=list(DTYPES))
    ap.add_argument("--max-shard-size", default="4GB")
    args = ap.parse_args()

    dtype = DTYPES[args.dtype]

    adapter_path = Path(args.adapter)
    if not adapter_path.exists():
        sys.exit(f"[FAIL] 어댑터 없음: {adapter_path}")
    if not (adapter_path / "adapter_config.json").exists():
        sys.exit(
            f"[FAIL] {adapter_path}/adapter_config.json 없음.\n"
            f"       학습이 끝났는지, output_dir 이 맞는지 확인하세요."
        )

    print(f"[MERGE] base     : {args.base}")
    print(f"[MERGE] adapter  : {adapter_path}")
    print(f"[MERGE] output   : {args.output}")
    print(f"[MERGE] dtype    : {args.dtype}")

    # transformers 4.56 (PR #39782) 부터 torch_dtype 은 deprecation,
    # dtype 가 정식 인자다. v5 에서 torch_dtype 이 제거되므로 dtype 로 부른다.
    base = AutoModelForCausalLM.from_pretrained(
        args.base,
        dtype=dtype,
        device_map="cpu",
        low_cpu_mem_usage=True,
    )
    print(f"[MERGE] 베이스 로드 완료 ({type(base).__name__})")

    model = PeftModel.from_pretrained(base, str(adapter_path))
    print("[MERGE] 어댑터 연결 완료, 병합 중...")
    model = model.merge_and_unload()
    print("[MERGE] 병합 완료")

    out_path = Path(args.output)
    out_path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out_path, safe_serialization=True, max_shard_size=args.max_shard_size)

    tok = AutoTokenizer.from_pretrained(args.base)
    tok.save_pretrained(out_path)

    shards = sorted(out_path.glob("*.safetensors"))
    total = sum(f.stat().st_size for f in shards) / (1024 ** 3)
    print(f"[OK] 병합 저장 완료: {out_path}")
    print(f"[OK] shard {len(shards)}개, 총 {total:.2f} GB")


if __name__ == "__main__":
    main()